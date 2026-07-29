"""Amazon Athena service widgets."""

from aws_tui.ui.widgets.athena.history_view import AthenaHistoryView
from aws_tui.ui.widgets.athena.page import AthenaPage
from aws_tui.ui.widgets.athena.query_view import AthenaQueryView
from aws_tui.ui.widgets.athena.results_view import AthenaResultsView
from aws_tui.ui.widgets.athena.saved_view import AthenaSavedView

__all__ = [
    "AthenaHistoryView",
    "AthenaPage",
    "AthenaQueryView",
    "AthenaResultsView",
    "AthenaSavedView",
]
