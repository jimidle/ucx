import ast
from abc import ABC, abstractmethod
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from databricks.labs.ucx.hive_metastore.migration_status import MigrationIndex
from databricks.labs.ucx.source_code.base import (
    Advice,
    Advisory,
    Deprecation,
    Fixer,
    Linter,
)
from databricks.labs.ucx.source_code.queries import FromTable

CLOUD_DIRECT_REFS = {
    "s3a://",
    "s3n://",
    "s3://",
    "wasb://",
    "wasbs://",
    "abfs://",
    "abfss://",
    "dbfs:/",
    "hdfs://",
    "file:/",
}


@dataclass
class Matcher(ABC):
    method_name: str
    min_args: int
    max_args: int
    table_arg_index: int
    table_arg_name: str | None = None

    def matches(self, node: ast.AST):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            return False
        return self._get_table_arg(node) is not None

    @abstractmethod
    def lint(self, from_table: FromTable, index: MigrationIndex, node: ast.Call) -> Iterator[Advice]:
        raise NotImplementedError()

    @abstractmethod
    def apply(self, from_table: FromTable, index: MigrationIndex, node: ast.Call) -> None:
        raise NotImplementedError()

    def _get_table_arg(self, node: ast.Call):
        if len(node.args) > 0:
            return node.args[self.table_arg_index] if self.min_args <= len(node.args) <= self.max_args else None
        assert self.table_arg_name is not None
        if not node.keywords:
            return None
        arg = next(kw for kw in node.keywords if kw.arg == self.table_arg_name)
        return arg.value if arg is not None else None


@dataclass
class QueryMatcher(Matcher):

    def lint(self, from_table: FromTable, index: MigrationIndex, node: ast.Call) -> Iterator[Advice]:
        table_arg = self._get_table_arg(node)
        if isinstance(table_arg, ast.Constant):
            for advice in from_table.lint(table_arg.value):
                yield advice.replace(
                    start_line=node.lineno,
                    start_col=node.col_offset,
                    end_line=node.end_lineno,
                    end_col=node.end_col_offset,
                )
        else:
            yield Advisory(
                code='table-migrate',
                message=f"Can't migrate '{node}' because its table name argument is not a constant",
                start_line=node.lineno,
                start_col=node.col_offset,
                end_line=node.end_lineno or 0,
                end_col=node.end_col_offset or 0,
            )

    def apply(self, from_table: FromTable, index: MigrationIndex, node: ast.Call) -> None:
        table_arg = self._get_table_arg(node)
        assert isinstance(table_arg, ast.Constant)
        new_query = from_table.apply(table_arg.value)
        table_arg.value = new_query


@dataclass
class TableNameMatcher(Matcher):

    def lint(self, from_table: FromTable, index: MigrationIndex, node: ast.Call) -> Iterator[Advice]:
        table_arg = self._get_table_arg(node)
        if isinstance(table_arg, ast.Constant):
            dst = self._find_dest(index, table_arg.value, from_table.schema)
            if dst is not None:
                yield Deprecation(
                    code='table-migrate',
                    message=f"Table {table_arg.value} is migrated to {dst.destination()} in Unity Catalog",
                    # SQLGlot does not propagate tokens yet. See https://github.com/tobymao/sqlglot/issues/3159
                    start_line=node.lineno,
                    start_col=node.col_offset,
                    end_line=node.end_lineno or 0,
                    end_col=node.end_col_offset or 0,
                )
        else:
            assert isinstance(node.func, ast.Attribute)  # always true, avoids a pylint warning
            yield Advisory(
                code='table-migrate',
                message=f"Can't migrate '{node.func.attr}' because its table name argument is not a constant",
                start_line=node.lineno,
                start_col=node.col_offset,
                end_line=node.end_lineno or 0,
                end_col=node.end_col_offset or 0,
            )

    def apply(self, from_table: FromTable, index: MigrationIndex, node: ast.Call) -> None:
        table_arg = self._get_table_arg(node)
        assert isinstance(table_arg, ast.Constant)
        dst = self._find_dest(index, table_arg.value, from_table.schema)
        if dst is not None:
            table_arg.value = dst.destination()

    @staticmethod
    def _find_dest(index: MigrationIndex, value: str, schema: str):
        parts = value.split(".")
        # Ensure that unqualified table references use the current schema
        if len(parts) == 1:
            return index.get(schema, parts[0])
        return None if len(parts) != 2 else index.get(parts[0], parts[1])


@dataclass
class ReturnValueMatcher(Matcher):

    def matches(self, node: ast.AST):
        return isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)

    def lint(self, from_table: FromTable, index: MigrationIndex, node: ast.Call) -> Iterator[Advice]:
        assert isinstance(node.func, ast.Attribute)  # always true, avoids a pylint warning
        yield Advisory(
            code='table-migrate',
            message=f"Call to '{node.func.attr}' will return a list of <catalog>.<database>.<table> instead of <database>.<table>.",
            start_line=node.lineno,
            start_col=node.col_offset,
            end_line=node.end_lineno or 0,
            end_col=node.end_col_offset or 0,
        )

    def apply(self, from_table: FromTable, index: MigrationIndex, node: ast.Call) -> None:
        raise NotImplementedError("Should never get there!")


@dataclass
class CloudAccessMatcher(Matcher):

    def matches(self, node: ast.AST):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            return False
        return self._get_table_arg(node) is not None

    def lint(self, from_table: FromTable, index: MigrationIndex, node: ast.Call) -> Iterator[Advice]:
        table_arg = self._get_table_arg(node)
        if isinstance(table_arg, ast.Constant):
            # check for cloud direct references
            if any(table_arg.value.startswith(prefix) for prefix in CLOUD_DIRECT_REFS):
                yield Deprecation(
                    code='cloud-access',
                    message=f"The use of cloud direct references is deprecated: '{table_arg.value}'",
                    start_line=node.lineno,
                    start_col=node.col_offset,
                    end_line=node.end_lineno or 0,
                    end_col=node.end_col_offset or 0,
                )
        # Do we wish to raise an advice for every use of the method that we
        # find does not use constant references? That probably pollutes the report.

    def apply(self, from_table: FromTable, index: MigrationIndex, node: ast.Call) -> None:
        # No transformations to apply
        return


class SparkMatchers:

    def __init__(self):
        # see https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.SparkSession.html
        spark_session_matchers = [QueryMatcher("sql", 1, 1000, 0, "sqlQuery"), TableNameMatcher("table", 1, 1, 0)]

        # see https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.Catalog.html
        spark_catalog_matchers = [
            TableNameMatcher("cacheTable", 1, 2, 0, "tableName"),
            TableNameMatcher("createTable", 1, 1000, 0, "tableName"),
            TableNameMatcher("createExternalTable", 1, 1000, 0, "tableName"),
            TableNameMatcher("getTable", 1, 1, 0),
            TableNameMatcher("isCached", 1, 1, 0),
            TableNameMatcher("listColumns", 1, 2, 0, "tableName"),
            TableNameMatcher("tableExists", 1, 2, 0, "tableName"),
            TableNameMatcher("recoverPartitions", 1, 1, 0),
            TableNameMatcher("refreshTable", 1, 1, 0),
            TableNameMatcher("uncacheTable", 1, 1, 0),
            ReturnValueMatcher("listTables", 0, 2, -1),
        ]

        # see https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.DataFrame.html
        spark_dataframe_matchers = [
            TableNameMatcher("writeTo", 1, 1, 0),
        ]

        # nothing to migrate in Column, see https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.Column.html
        # nothing to migrate in Observation, see https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.Observation.html
        # nothing to migrate in Row, see https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.Row.html
        # nothing to migrate in GroupedData, see https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.GroupedData.html
        # nothing to migrate in PandasCogroupedOps, see https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.PandasCogroupedOps.html
        # nothing to migrate in DataFrameNaFunctions, see https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.DataFrameNaFunctions.html
        # nothing to migrate in DataFrameStatFunctions, see https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.DataFrameStatFunctions.html
        # nothing to migrate in Window, see https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.Window.html

        # see https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.DataFrameReader.html
        spark_dataframereader_matchers = [
            TableNameMatcher("table", 1, 1, 0),  # TODO good example of collision, see spark_session_calls
        ]

        # see https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.DataFrameWriter.html
        spark_dataframewriter_matchers = [
            TableNameMatcher("insertInto", 1, 2, 0, "tableName"),
            # TODO jdbc: could the url be a databricks url, raise warning ?
            TableNameMatcher("saveAsTable", 1, 4, 0, "name"),
        ]

        # nothing to migrate in DataFrameWriterV2, see https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.DataFrameWriterV2.html
        # nothing to migrate in UDFRegistration, see https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.UDFRegistration.html

        # see https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.UDTFRegistration.html
        spark_udtfregistration_matchers = [
            TableNameMatcher("register", 1, 2, 0, "name"),
        ]

        spark_cloud_matchers = [
            CloudAccessMatcher("ls", 1, 1, 0),
            CloudAccessMatcher("cp", 1, 2, 0),
            CloudAccessMatcher("rm", 1, 1, 0),
            CloudAccessMatcher("head", 1, 1, 0),
            CloudAccessMatcher("put", 1, 2, 0),
            CloudAccessMatcher("mkdirs", 1, 1, 0),
            CloudAccessMatcher("move", 1, 2, 0),
            CloudAccessMatcher("text", 1, 3, 0),
            CloudAccessMatcher("csv", 1, 1000, 0),
            CloudAccessMatcher("json", 1, 1000, 0),
            CloudAccessMatcher("orc", 1, 1000, 0),
            CloudAccessMatcher("parquet", 1, 1000, 0),
            CloudAccessMatcher("save", 0, 1000, -1, "path"),
            CloudAccessMatcher("load", 0, 1000, -1, "path"),
            CloudAccessMatcher("option", 1, 1000, 1),  # Only .option("path", "xxx://bucket/path") will hit
            CloudAccessMatcher("addFile", 1, 3, 0),
            CloudAccessMatcher("binaryFiles", 1, 2, 0),
            CloudAccessMatcher("binaryRecords", 1, 2, 0),
            CloudAccessMatcher("dump_profiles", 1, 1, 0),
            CloudAccessMatcher("hadoopFile", 1, 8, 0),
            CloudAccessMatcher("newAPIHadoopFile", 1, 8, 0),
            CloudAccessMatcher("pickleFile", 1, 3, 0),
            CloudAccessMatcher("saveAsHadoopFile", 1, 8, 0),
            CloudAccessMatcher("saveAsNewAPIHadoopFile", 1, 7, 0),
            CloudAccessMatcher("saveAsPickleFile", 1, 2, 0),
            CloudAccessMatcher("saveAsSequenceFile", 1, 2, 0),
            CloudAccessMatcher("saveAsTextFile", 1, 2, 0),
            CloudAccessMatcher("load_from_path", 1, 1, 0),
        ]

        # nothing to migrate in UserDefinedFunction, see https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.UserDefinedFunction.html
        # nothing to migrate in UserDefinedTableFunction, see https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.UserDefinedTableFunction.html
        self._matchers = {}
        for matcher in (
            spark_session_matchers
            + spark_catalog_matchers
            + spark_dataframe_matchers
            + spark_dataframereader_matchers
            + spark_dataframewriter_matchers
            + spark_udtfregistration_matchers
            + spark_cloud_matchers
        ):
            self._matchers[matcher.method_name] = matcher

    @property
    def matchers(self):
        return self._matchers


class SparkSql(Linter, Fixer):

    _spark_matchers = SparkMatchers()

    def __init__(self, from_table: FromTable, index: MigrationIndex):
        self._from_table = from_table
        self._index = index

    def name(self) -> str:
        # this is the same fixer, just in a different language context
        return self._from_table.name()

    def lint(self, code: str) -> Iterable[Advice]:
        tree = ast.parse(code)
        for node in ast.walk(tree):
            matcher = self._find_matcher(node)
            if matcher is None:
                continue
            assert isinstance(node, ast.Call)
            yield from matcher.lint(self._from_table, self._index, node)

    def apply(self, code: str) -> str:
        tree = ast.parse(code)
        # we won't be doing it like this in production, but for the sake of the example
        for node in ast.walk(tree):
            matcher = self._find_matcher(node)
            if matcher is None:
                continue
            assert isinstance(node, ast.Call)
            matcher.apply(self._from_table, self._index, node)
        return ast.unparse(tree)

    def _find_matcher(self, node: ast.AST):
        if not isinstance(node, ast.Call):
            return None
        if not isinstance(node.func, ast.Attribute):
            return None
        matcher = self._spark_matchers.matchers.get(node.func.attr, None)
        if matcher is None:
            return None
        return matcher if matcher.matches(node) else None
