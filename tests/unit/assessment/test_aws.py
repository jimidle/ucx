import logging
from unittest.mock import create_autospec

import pytest
from databricks.labs.blueprint.installation import MockInstallation
from databricks.sdk import WorkspaceClient
from databricks.sdk.service import iam
from databricks.sdk.service.compute import InstanceProfile

from databricks.labs.ucx.assessment.aws import (
    AWSInstanceProfile,
    AWSPolicyAction,
    AWSResourcePermissions,
    AWSResources,
    AWSRole,
    run_command,
)

logger = logging.getLogger(__name__)


def test_aws_validate():
    successful_return = """
    {
        "UserId": "uu@mail.com",
        "Account": "1234",
        "Arn": "arn:aws:sts::1234:assumed-role/AWSVIEW/uu@mail.com"
    }
    """

    def successful_call(cmd: str):
        return 0, successful_return, ""

    aws = AWSResources("Fake_Profile", successful_call)
    assert aws.validate_connection()

    def failed_call(cmd: str):
        return -1, "", "Can't connect"

    aws = AWSResources("Fake_Profile", failed_call)
    assert not aws.validate_connection()


def test_list_role_policies():
    command_return = """
{
    "PolicyNames": [
        "Policy1",
        "Policy2",
        "Policy3"
    ],
    "IsTruncated": false
}

    """

    def command_call(cmd: str):
        return 0, command_return, ""

    aws = AWSResources("Fake_Profile", command_call)
    role_policies = aws.list_role_policies("fake_role")
    assert role_policies == ["Policy1", "Policy2", "Policy3"]


def test_list_attached_policies_in_role():
    command_return = """
{
    "AttachedPolicies": [
        {
            "PolicyName": "Policy1",
            "PolicyArn": "arn:aws:iam::aws:policy/Policy1"
        },
        {
            "PolicyName": "Policy2",
            "PolicyArn": "arn:aws:iam::aws:policy/Policy2"
        }
    ],
    "IsTruncated": false
}

    """

    def command_call(cmd: str):
        return 0, command_return, ""

    aws = AWSResources("Fake_Profile", command_call)
    role_policies = aws.list_attached_policies_in_role("fake_role")
    assert role_policies == ["arn:aws:iam::aws:policy/Policy1", "arn:aws:iam::aws:policy/Policy2"]


def test_get_role_policy():
    get_role_policy_return = """
{
    "RoleName": "Role",
    "PolicyName": "Policy",
    "PolicyDocument": {
        "Version": "2024-01-01",
        "Statement": [
                {
                    "Effect": "Allow",
                    "Action": [
                        "s3:GetObject"
                    ],
                    "Resource": [
                        "arn:aws:s3:::bucket1/*",
                        "arn:aws:s3:::bucket2/*",
                        "arn:aws:s3:::bucket3/*"
                    ]
                }
        ]
    }
}

    """

    get_policy_return = """
{
    "Policy": {
        "PolicyName": "policy",
        "PolicyId": "1234",
        "Arn": "arn:aws:iam::12345:policy/policy",
        "Path": "/",
        "DefaultVersionId": "v1",
        "AttachmentCount": 1,
        "PermissionsBoundaryUsageCount": 0,
        "IsAttachable": true,
        "Description": "Policy Description",
        "CreateDate": "2024-01-01T00:00:00+00:00",
        "UpdateDate": "2024-01-01T00:00:00+00:00",
        "Tags": []
    }
}
    """

    get_policy_version_return = """
{
    "PolicyVersion": {
        "Document": {
            "Version": "V1",
            "Statement": [
                {
                    "Sid": "Permit",
                    "Effect": "Allow",
                    "Action": [
                        "s3:PutObject",
                        "s3:GetObject",
                        "s3:DeleteObject",
                        "s3:PutObjectAcl",
                        "s3:GetBucketNotification",
                        "s3:PutBucketNotification"
                    ],
                    "Resource": [
                        "arn:aws:s3:::bucketA/*",
                        "arn:aws:s3:::bucketB/*",
                        "arn:aws:s3:::bucketC/*"
                    ]
                }
            ]
        },
        "VersionId": "v1",
        "IsDefaultVersion": true,
        "CreateDate": "2024-01-01T00:00:00+00:00"
    }
}
    """

    def command_call(cmd: str):
        if "iam get-role-policy" in cmd:
            return 0, get_role_policy_return, ""
        elif "iam get-policy " in cmd:
            return 0, get_policy_return, ""
        elif "iam get-policy-version" in cmd:
            return 0, get_policy_version_return, ""
        else:
            return -1, "", "Error"

    aws = AWSResources("Fake_Profile", command_call)
    role_policy = aws.get_role_policy("fake_role", policy_name="fake_policy")
    assert role_policy == [
        AWSPolicyAction(
            resource_type="s3",
            privilege="READ_FILES",
            resource_path="s3://bucket1",
        ),
        AWSPolicyAction(
            resource_type="s3",
            privilege="READ_FILES",
            resource_path="s3a://bucket1",
        ),
        AWSPolicyAction(
            resource_type="s3",
            privilege="READ_FILES",
            resource_path="s3://bucket2",
        ),
        AWSPolicyAction(
            resource_type="s3",
            privilege="READ_FILES",
            resource_path="s3a://bucket2",
        ),
        AWSPolicyAction(
            resource_type="s3",
            privilege="READ_FILES",
            resource_path="s3://bucket3",
        ),
        AWSPolicyAction(
            resource_type="s3",
            privilege="READ_FILES",
            resource_path="s3a://bucket3",
        ),
    ]

    role_policy = aws.get_role_policy("fake_role", attached_policy_arn="arn:aws:iam::12345:policy/policy")
    assert role_policy == [
        AWSPolicyAction(
            resource_type="s3",
            privilege="WRITE_FILES",
            resource_path="s3://bucketA",
        ),
        AWSPolicyAction(
            resource_type="s3",
            privilege="WRITE_FILES",
            resource_path="s3a://bucketA",
        ),
        AWSPolicyAction(
            resource_type="s3",
            privilege="WRITE_FILES",
            resource_path="s3://bucketB",
        ),
        AWSPolicyAction(
            resource_type="s3",
            privilege="WRITE_FILES",
            resource_path="s3a://bucketB",
        ),
        AWSPolicyAction(
            resource_type="s3",
            privilege="WRITE_FILES",
            resource_path="s3://bucketC",
        ),
        AWSPolicyAction(
            resource_type="s3",
            privilege="WRITE_FILES",
            resource_path="s3a://bucketC",
        ),
    ]


def test_get_uc_roles():
    list_roles_return = """
    {
        "Roles": [
            {
                "Path": "/",
                "RoleName": "NON-UC-Role",
                "RoleId": "12345",
                "Arn": "arn:aws:iam::123456789:role/non-uc-role",
                "CreateDate": "2024-01-01T00:00:00+00:00",
                "AssumeRolePolicyDocument": {
                    "Version": "2024-01-01",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {
                                "Service": "ec2.amazonaws.com"
                            },
                            "Action": "sts:AssumeRole"
                        }
                    ]
                },
                "MaxSessionDuration": 3600
            },
            {
                "Path": "/",
                "RoleName": "uc-role-1",
                "RoleId": "12345",
                "Arn": "arn:aws:iam::123456789:role/uc-role-1",
                "CreateDate": "2024-01-01T00:00:00+00:00",
                "AssumeRolePolicyDocument": {
                    "Version": "2024-01-01",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {
                                "AWS": [
                                    "arn:aws:iam::123456789:role/uc-role",
                                    "arn:aws:iam::414351767826:role/unity-catalog-prod-UCMasterRole-14S5ZJVKOTYTL"
                                ]
                            },
                            "Action": "sts:AssumeRole",
                            "Condition": {
                                "StringEquals": {
                                    "sts:ExternalId": "1122334455"
                                }
                            }
                        }
                    ]
                }
            },
            {
                "Path": "/",
                "RoleName": "uc-role-2",
                "RoleId": "123456",
                "Arn": "arn:aws:iam::123456789:role/uc-role-2",
                "CreateDate": "2024-01-01T00:00:00+00:00",
                "AssumeRolePolicyDocument": {
                    "Version": "2024-01-01",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {
                                "AWS": "arn:aws:iam::414351767826:role/unity-catalog-prod-UCMasterRole-14S5ZJVKOTYTL"
                            },
                            "Action": "sts:AssumeRole",
                            "Condition": {
                                "StringEquals": {
                                    "sts:ExternalId": "1122334466"
                                }
                            }
                        }
                    ]
                }
            }
        ]
    }
    """

    def command_call(cmd: str):
        return 0, list_roles_return, ""

    aws = AWSResources("Fake_Profile", command_call)
    uc_roles = aws.list_all_uc_roles()
    assert uc_roles == [
        AWSRole(path='/', role_name='uc-role-1', role_id='12345', arn='arn:aws:iam::123456789:role/uc-role-1'),
        AWSRole(path='/', role_name='uc-role-2', role_id='123456', arn='arn:aws:iam::123456789:role/uc-role-2'),
    ]


def test_get_uc_roles_missing_keys():
    list_roles_return = """
    {
        "Roles": [
            {
                "Path": "/",
                "RoleName": "NON-UC-Role",
                "RoleId": "12345",
                "Arn": "arn:aws:iam::123456789:role/non-uc-role",
                "CreateDate": "2024-01-01T00:00:00+00:00",
                "AssumeRolePolicyDocument": {
                    "Version": "2024-01-01",
                    "Statement": [
                        {
                            "Defect": "Allow",
                            "Principal": {
                                "Service": "ec2.amazonaws.com"
                            },
                            "Action": "sts:AssumeRole"
                        }
                    ]
                },
                "MaxSessionDuration": 3600
            },
            {
                "Path": "/",
                "RoleName": "uc-role-1",
                "RoleId": "12345",
                "Arn": "arn:aws:iam::123456789:role/uc-role-1",
                "CreateDate": "2024-01-01T00:00:00+00:00",
                "AssumeRolePolicyDocument": {
                    "Version": "2024-01-01",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {
                                "AWS": [
                                    "arn:aws:iam::123456789:role/uc-role",
                                    "arn:aws:iam::414351767826:role/unity-catalog-prod-UCMasterRole-14S5ZJVKOTYTL"
                                ]
                            },
                            "Non-Action": "sts:AssumeRole",
                            "Condition": {
                                "StringEquals": {
                                    "sts:ExternalId": "1122334455"
                                }
                            }
                        }
                    ]
                }
            },
            {
                "Path": "/",
                "RoleName": "uc-role-2",
                "RoleId": "123456",
                "Arn": "arn:aws:iam::123456789:role/uc-role-2",
                "CreateDate": "2024-01-01T00:00:00+00:00",
                "AssumeRolePolicyDocument": {
                    "Version": "2024-01-01",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {
                                "AWS": "arn:aws:iam::414351767826:role/unity-catalog-prod-UCMasterRole-14S5ZJVKOTYTL"
                            },
                            "Action": "sts:DontAssumeRole",
                            "Condition": {
                                "StringEquals": {
                                    "sts:ExternalId": "1122334466"
                                }
                            }
                        }
                    ]
                }
            },
            {
                "Path": "/",
                "RoleName": "uc-role-3",
                "RoleId": "123456",
                "Arn": "arn:aws:iam::123456789:role/uc-role-3",
                "CreateDate": "2024-01-01T00:00:00+00:00"

            },
            {
                "Path": "/",
                "RoleName": "uc-role-4",
                "RoleId": "123456",
                "Arn": "arn:aws:iam::123456789:role/uc-role-4",
                "CreateDate": "2024-01-01T00:00:00+00:00",
                "AssumeRolePolicyDocument": {
                    "Version": "2024-01-01",
                    "Statement": [
                        {
                            "Effect": "Deny",
                            "Principal": {
                                "AWS": "arn:aws:iam::414351767826:role/unity-catalog-prod-UCMasterRole-14S5ZJVKOTYTL"
                            },
                            "Action": "sts:DontAssumeRole",
                            "Condition": {
                                "StringEquals": {
                                    "sts:ExternalId": "1122334466"
                                }
                            }
                        }
                    ]
                }
            },
            {
                "Path": "/",
                "RoleName": "uc-role-5",
                "RoleId": "12345",
                "Arn": "arn:aws:iam::123456789:role/uc-role-5",
                "CreateDate": "2024-01-01T00:00:00+00:00",
                "AssumeRolePolicyDocument": {
                    "Version": "2024-01-01",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {
                                "AWS": [
                                    "arn:aws:iam::123456789:role/uc-role-5",
                                    "arn:aws:iam::123456789:role/another-role"
                                ]
                            },
                            "Action": "sts:AssumeRole",
                            "Condition": {
                                "StringEquals": {
                                    "sts:ExternalId": "1122334455"
                                }
                            }
                        }
                    ]
                }
            },
            {
                "Path": "/",
                "RoleName": "uc-role-6",
                "RoleId": "12345",
                "Arn": "arn:aws:iam::123456789:role/uc-role-6",
                "CreateDate": "2024-01-01T00:00:00+00:00",
                "AssumeRolePolicyDocument": {
                    "Version": "2024-01-01",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {
                                "AWS": "arn:aws:iam::123456789:role/uc-role-5"
                            },
                            "Action": "sts:AssumeRole",
                            "Condition": {
                                "StringEquals": {
                                    "sts:ExternalId": "1122334455"
                                }
                            }
                        }
                    ]
                }
            }
        ]
    }
    """

    def command_call(cmd: str):
        return 0, list_roles_return, ""

    aws = AWSResources("Fake_Profile", command_call)
    uc_roles = aws.list_all_uc_roles()
    assert not uc_roles


def test_save_instance_profile_permissions():
    ws = create_autospec(WorkspaceClient)
    ws.instance_profiles.list.return_value = [
        InstanceProfile("arn:aws:iam::12345:instance-profile/role1", "arn:aws:iam::12345:role/role1")
    ]
    aws = create_autospec(AWSResources)
    aws.get_role_policy.side_effect = [
        [
            AWSPolicyAction(
                resource_type="s3",
                privilege="READ_FILES",
                resource_path="s3://bucket1",
            ),
            AWSPolicyAction(
                resource_type="s3",
                privilege="READ_FILES",
                resource_path="s3://bucket2",
            ),
            AWSPolicyAction(
                resource_type="s3",
                privilege="READ_FILES",
                resource_path="s3://bucket3",
            ),
        ],
        [],
        [],
        [
            AWSPolicyAction(
                resource_type="s3",
                privilege="WRITE_FILES",
                resource_path="s3://bucketA",
            ),
            AWSPolicyAction(
                resource_type="s3",
                privilege="WRITE_FILES",
                resource_path="s3://bucketB",
            ),
            AWSPolicyAction(
                resource_type="s3",
                privilege="WRITE_FILES",
                resource_path="s3://bucketC",
            ),
        ],
        [],
        [],
    ]
    aws.list_role_policies.return_value = ["Policy1", "Policy2", "Policy3"]
    aws.list_attached_policies_in_role.return_value = [
        "arn:aws:iam::aws:policy/Policy1",
        "arn:aws:iam::aws:policy/Policy2",
    ]

    installation = MockInstallation()
    aws_resource_permissions = AWSResourcePermissions(installation, ws, aws)
    aws_resource_permissions.save_instance_profile_permissions()

    installation.assert_file_written(
        'aws_instance_profile_info.csv',
        [
            {
                'role_arn': 'arn:aws:iam::12345:instance-profile/role1',
                'privilege': 'READ_FILES',
                'resource_path': 's3://bucket1',
                'resource_type': 's3',
            },
            {
                'role_arn': 'arn:aws:iam::12345:instance-profile/role1',
                'privilege': 'READ_FILES',
                'resource_path': 's3://bucket2',
                'resource_type': 's3',
            },
            {
                'role_arn': 'arn:aws:iam::12345:instance-profile/role1',
                'privilege': 'READ_FILES',
                'resource_path': 's3://bucket3',
                'resource_type': 's3',
            },
            {
                'role_arn': 'arn:aws:iam::12345:instance-profile/role1',
                'privilege': 'WRITE_FILES',
                'resource_path': 's3://bucketA',
                'resource_type': 's3',
            },
            {
                'role_arn': 'arn:aws:iam::12345:instance-profile/role1',
                'privilege': 'WRITE_FILES',
                'resource_path': 's3://bucketB',
                'resource_type': 's3',
            },
            {
                'role_arn': 'arn:aws:iam::12345:instance-profile/role1',
                'privilege': 'WRITE_FILES',
                'resource_path': 's3://bucketC',
                'resource_type': 's3',
            },
        ],
    )


def test_save_uc_compatible_roles():
    ws = create_autospec(WorkspaceClient)
    ws.current_user.me = lambda: iam.User(user_name="me@example.com", groups=[iam.ComplexValue(display="admins")])

    aws = create_autospec(AWSResources)
    aws.get_role_policy.side_effect = [
        [
            AWSPolicyAction(
                resource_type="s3",
                privilege="READ_FILES",
                resource_path="s3://bucket1",
            ),
            AWSPolicyAction(
                resource_type="s3",
                privilege="READ_FILES",
                resource_path="s3://bucket2",
            ),
            AWSPolicyAction(
                resource_type="s3",
                privilege="READ_FILES",
                resource_path="s3://bucket3",
            ),
        ],
        [],
        [],
        [
            AWSPolicyAction(
                resource_type="s3",
                privilege="WRITE_FILES",
                resource_path="s3://bucketA",
            ),
            AWSPolicyAction(
                resource_type="s3",
                privilege="WRITE_FILES",
                resource_path="s3://bucketB",
            ),
            AWSPolicyAction(
                resource_type="s3",
                privilege="WRITE_FILES",
                resource_path="s3://bucketC",
            ),
        ],
        [],
        [],
    ]
    aws.list_role_policies.return_value = ["Policy1", "Policy2", "Policy3"]
    aws.list_attached_policies_in_role.return_value = [
        "arn:aws:iam::aws:policy/Policy1",
        "arn:aws:iam::aws:policy/Policy2",
    ]
    aws.list_all_uc_roles.return_value = [
        AWSRole(path='/', role_name='uc-role1', role_id='12345', arn='arn:aws:iam::12345:role/uc-role1')
    ]

    installation = MockInstallation()
    aws_resource_permissions = AWSResourcePermissions(installation, ws, aws)
    aws_resource_permissions.save_uc_compatible_roles()
    installation.assert_file_written(
        'uc_roles_access.csv',
        [
            {
                'privilege': 'READ_FILES',
                'resource_path': 's3://bucket1',
                'resource_type': 's3',
                'role_arn': 'arn:aws:iam::12345:role/uc-role1',
            },
            {
                'privilege': 'READ_FILES',
                'resource_path': 's3://bucket2',
                'resource_type': 's3',
                'role_arn': 'arn:aws:iam::12345:role/uc-role1',
            },
            {
                'privilege': 'READ_FILES',
                'resource_path': 's3://bucket3',
                'resource_type': 's3',
                'role_arn': 'arn:aws:iam::12345:role/uc-role1',
            },
            {
                'privilege': 'WRITE_FILES',
                'resource_path': 's3://bucketA',
                'resource_type': 's3',
                'role_arn': 'arn:aws:iam::12345:role/uc-role1',
            },
            {
                'privilege': 'WRITE_FILES',
                'resource_path': 's3://bucketB',
                'resource_type': 's3',
                'role_arn': 'arn:aws:iam::12345:role/uc-role1',
            },
            {
                'privilege': 'WRITE_FILES',
                'resource_path': 's3://bucketC',
                'resource_type': 's3',
                'role_arn': 'arn:aws:iam::12345:role/uc-role1',
            },
        ],
    )


def test_role_mismatched(caplog):
    instance_profile = AWSInstanceProfile("test", "fake")
    assert not instance_profile.role_name
    assert "Role ARN is mismatched" in caplog.messages[0]


def test_get_role_policy_missing_role(caplog):
    def command_call(cmd: str):
        return 0, "", ""

    aws = AWSResources("Fake_Profile", command_call)

    aws.get_role_policy("fake_role")
    assert "No role name or attached role ARN specified." in caplog.messages[0]


def test_instance_profiles_empty_mapping(caplog):
    ws = create_autospec(WorkspaceClient)
    ws.instance_profiles.list.return_value = [
        InstanceProfile("arn:aws:iam::12345:instance-profile/role1", "arn:aws:iam::12345:role/role1")
    ]
    ws.current_user.me = lambda: iam.User(user_name="me@example.com", groups=[iam.ComplexValue(display="admins")])
    aws = create_autospec(AWSResources)
    installation = MockInstallation()
    aws_resource_permissions = AWSResourcePermissions(installation, ws, aws)
    aws_resource_permissions.save_instance_profile_permissions()
    assert 'No Mapping Was Generated.' in caplog.messages


def test_uc_roles_empty_mapping(caplog):
    ws = create_autospec(WorkspaceClient)
    ws.instance_profiles.list.return_value = [
        InstanceProfile("arn:aws:iam::12345:instance-profile/role1", "arn:aws:iam::12345:role/role1")
    ]
    ws.current_user.me = lambda: iam.User(user_name="me@example.com", groups=[iam.ComplexValue(display="admins")])
    aws = create_autospec(AWSResources)
    installation = MockInstallation()
    aws_resource_permissions = AWSResourcePermissions(installation, ws, aws)
    aws_resource_permissions.save_uc_compatible_roles()
    assert 'No Mapping Was Generated.' in caplog.messages


def test_command(caplog):
    return_code, output, error = run_command("echo success")
    assert return_code == 0
    with pytest.raises(FileNotFoundError) as exception:
        run_command("no_way_this_command_would_work")
        print(exception)