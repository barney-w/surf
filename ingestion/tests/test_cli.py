"""Tests for the ingestion CLI commands."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from click.testing import CliRunner

from src.connectors.sharepoint_sync import SyncResult
from src.main import cli

_SP_MODULE = "src.connectors.sharepoint_sync"


# ---------------------------------------------------------------------------
# sync-sharepoint command
# ---------------------------------------------------------------------------


class TestSyncSharepointCommand:
    def test_dry_run_invokes_sync(self) -> None:
        """The --dry-run flag should invoke sync without requiring storage."""
        mock_result = SyncResult(files_synced=2, pages_synced=1)
        mock_config = MagicMock()
        mock_config.site_url = "https://example.sharepoint.com"

        with (
            patch(
                f"{_SP_MODULE}.SyncConfig.from_env",
                return_value=mock_config,
            ),
            patch(
                f"{_SP_MODULE}.SharePointSync",
            ) as mock_cls,
        ):
            mock_instance = mock_cls.return_value
            mock_instance.sync = AsyncMock(return_value=mock_result)

            runner = CliRunner()
            result = runner.invoke(cli, ["sync-sharepoint", "--dry-run"])

        assert result.exit_code == 0, result.output
        assert "Files synced" in result.output
        mock_instance.sync.assert_called_once_with(dry_run=True)

    def test_missing_env_shows_error(self) -> None:
        """Missing SHAREPOINT_SITE_URL should show an error."""
        with patch(
            f"{_SP_MODULE}.SyncConfig.from_env",
            side_effect=ValueError("SHAREPOINT_SITE_URL environment variable is required"),
        ):
            runner = CliRunner()
            result = runner.invoke(cli, ["sync-sharepoint"])

        assert result.exit_code != 0
        assert "SHAREPOINT_SITE_URL" in result.output

    def test_sync_error_displayed(self) -> None:
        """Errors during sync should be displayed in the summary."""
        mock_result = SyncResult(
            files_synced=1,
            pages_synced=0,
            errors=["Failed to sync report.pdf: network error"],
        )
        mock_config = MagicMock()
        mock_config.site_url = "https://example.sharepoint.com"

        with (
            patch(
                f"{_SP_MODULE}.SyncConfig.from_env",
                return_value=mock_config,
            ),
            patch(
                f"{_SP_MODULE}.SharePointSync",
            ) as mock_cls,
        ):
            mock_instance = mock_cls.return_value
            mock_instance.sync = AsyncMock(return_value=mock_result)

            runner = CliRunner()
            result = runner.invoke(cli, ["sync-sharepoint"])

        assert result.exit_code == 0, result.output
        assert "Errors           : 1" in result.output
        assert "report.pdf" in result.output

    def test_displays_deleted_counts(self) -> None:
        """Deleted file/page counts should appear in the summary."""
        mock_result = SyncResult(
            files_synced=0,
            pages_synced=0,
            files_deleted=3,
            pages_deleted=1,
        )
        mock_config = MagicMock()
        mock_config.site_url = "https://example.sharepoint.com"

        with (
            patch(
                f"{_SP_MODULE}.SyncConfig.from_env",
                return_value=mock_config,
            ),
            patch(
                f"{_SP_MODULE}.SharePointSync",
            ) as mock_cls,
        ):
            mock_instance = mock_cls.return_value
            mock_instance.sync = AsyncMock(return_value=mock_result)

            runner = CliRunner()
            result = runner.invoke(cli, ["sync-sharepoint"])

        assert result.exit_code == 0, result.output
        assert "Files deleted    : 3" in result.output
        assert "Pages deleted    : 1" in result.output

    def test_displays_sensitivity_skipped_count(self) -> None:
        """Sensitivity-skipped file count should appear in the summary."""
        mock_result = SyncResult(
            files_synced=1,
            pages_synced=0,
            files_skipped_sensitivity=5,
        )
        mock_config = MagicMock()
        mock_config.site_url = "https://example.sharepoint.com"

        with (
            patch(
                f"{_SP_MODULE}.SyncConfig.from_env",
                return_value=mock_config,
            ),
            patch(
                f"{_SP_MODULE}.SharePointSync",
            ) as mock_cls,
        ):
            mock_instance = mock_cls.return_value
            mock_instance.sync = AsyncMock(return_value=mock_result)

            runner = CliRunner()
            result = runner.invoke(cli, ["sync-sharepoint"])

        assert result.exit_code == 0, result.output
        assert "Files (sensitive): 5" in result.output

    def test_displays_oversized_count(self) -> None:
        """Oversized file count should appear in the summary."""
        mock_result = SyncResult(
            files_synced=0,
            pages_synced=0,
            files_oversized=2,
        )
        mock_config = MagicMock()
        mock_config.site_url = "https://example.sharepoint.com"

        with (
            patch(
                f"{_SP_MODULE}.SyncConfig.from_env",
                return_value=mock_config,
            ),
            patch(
                f"{_SP_MODULE}.SharePointSync",
            ) as mock_cls,
        ):
            mock_instance = mock_cls.return_value
            mock_instance.sync = AsyncMock(return_value=mock_result)

            runner = CliRunner()
            result = runner.invoke(cli, ["sync-sharepoint"])

        assert result.exit_code == 0, result.output
        assert "Files oversized  : 2" in result.output
