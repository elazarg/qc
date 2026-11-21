"""The earlier name of `lab.learner.teach`, kept so that `calibration.open(seat)` goes on working.

Nothing is calibrated there: the app is taught to tell a control's settings
apart by sound. The window is `lab.learner.teach`; what it keeps and fits is
`lab.learner.taught`.
"""

from lab.learner.teach import open  # noqa: A004,F401 - the name the desk calls
