"""New-job presentation reuses the reviewed editor without clone claims."""

import pytest
from textual.app import App
from textual.widgets import Static
from vmx import MessageHub

from aws_tui.domain.mutation_guard import PROTECTED_MESSAGE, ConnectionProtectedError
from aws_tui.ui.widgets.emr_serverless.clone_modal import JobRunCloneModal, _submission_error
from tests.unit.vm.emr_serverless.test_new_job_vm import make


def test_protection_refusal_explains_settings_instead_of_iam_permissions():
    assert _submission_error(ConnectionProtectedError(PROTECTED_MESSAGE)) == PROTECTED_MESSAGE


@pytest.mark.asyncio
async def test_blank_modal_has_new_job_titles_and_inline_required_error():
    vm, _ = make()
    app = App()
    try:
        async with app.run_test(size=(80, 24)):
            modal = JobRunCloneModal(vm, hub=MessageHub())
            await app.push_screen(modal)
            assert str(modal.query_one("#clone-title", Static).render()) == "New Spark job"
            modal.action_review()
            assert not modal.reviewing
            assert "execution role" in str(modal.query_one("#clone-error", Static).render())
            modal.action_cancel()
    finally:
        vm.dispose()
