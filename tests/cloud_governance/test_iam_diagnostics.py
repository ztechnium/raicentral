"""Unit tests for GCP IAM diagnostics."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from rai_gateway.cloud_governance.providers.gcp import iam_diagnostics


@patch("googleapiclient.discovery.build")
def test_test_iam_permissions_uses_project_id_not_full_resource(mock_build):
    mock_service = MagicMock()
    mock_projects = MagicMock()
    mock_test = MagicMock()
    mock_test.execute.return_value = {"permissions": ["resourcemanager.projects.get"]}
    mock_projects.testIamPermissions.return_value = mock_test
    mock_service.projects.return_value = mock_projects
    mock_build.return_value = mock_service

    creds = MagicMock()
    creds.universe_domain = "googleapis.com"
    result = iam_diagnostics.test_project_permissions(
        project_id="lapeep-account-2",
        credentials=creds,
        permissions=("resourcemanager.projects.get",),
    )

    mock_projects.testIamPermissions.assert_called_once_with(
        resource="lapeep-account-2",
        body={"permissions": ["resourcemanager.projects.get"]},
    )
    assert result["resource"] == "projects/lapeep-account-2"
    assert "resourcemanager.projects.get" in result["granted"]


@patch("googleapiclient.discovery.build")
def test_test_iam_permissions_strips_projects_prefix(mock_build):
    mock_service = MagicMock()
    mock_projects = MagicMock()
    mock_test = MagicMock()
    mock_test.execute.return_value = {"permissions": []}
    mock_projects.testIamPermissions.return_value = mock_test
    mock_service.projects.return_value = mock_projects
    mock_build.return_value = mock_service

    creds = MagicMock()
    creds.universe_domain = "googleapis.com"
    iam_diagnostics.test_project_permissions(
        project_id="projects/lapeep-account-2",
        credentials=creds,
        permissions=("resourcemanager.projects.get",),
    )

    mock_projects.testIamPermissions.assert_called_once_with(
        resource="lapeep-account-2",
        body={"permissions": ["resourcemanager.projects.get"]},
    )
