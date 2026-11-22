"""Learning the guitar that is played: tag dry recordings with the guitar's pickup, mini-toggle and knobs and
have the app tell them from the sound alone (the Guitar Learner), or teach it one control's settings and map a
return gesture on it (Teach a control).

    from lab.learner.screen import LearningScreen
    screen = LearningScreen(seat, parent)     # seat: lab.learner.seat.LearnerSeat, made by the desk
    from lab.learner import offered_for
    offered_for("Charvel")                    # the control layouts the Guitar Learner has for an instrument

    screen         the learning screen, in the place of the desk below its top row: its two views, chosen in
                   place, on the instrument chosen on the desk
    window         the Guitar Learner, one of the two views
    report         what it says of its checks and of an estimate, without anything on show
    teach          Teach a control, the other view, with the gesture mapping
    taught         what was taught about one control, and checking a take against it, without anything on show
    gestures       a return gesture on a guitar control turns a Shape knob: gestures.start(seat), .state(), .stop()
    compensation   whether a high shelf can make up for a rolled-back tone pot
    board          the guitar's controls, drawn on a photograph of it
    dataset        labelled dry excerpts, kept as copies: the one store of examples
    configuration  fitting a guitar's configuration to the examples, and estimating it from a clip
    state          pitch and harmonic readings of mono dry audio, and telling one control's settings apart
    profiles       the fitted profiles as files: one check of their shape, one save, one load
    continuous     following live playing, and noticing a change of setup on a held note
    live           the live listening loop, run inside the worker process
    worker         that process (live listening or gesture listening), and its supervisor on the desk's side
    takes          recording a take
    inputs         where the dry guitar arrives (asked of the engine), and the recording held open until Stop
    seat           what all of this asks of the desk: the data folder, the audio hold, the desk's clock
    folders        where everything is kept, and the one-time copy out of the program folder
"""

__all__ = ["GuitarLearner", "offered_for"]


def __getattr__(name):
    # What is shown brings in Tk; the worker process and the analysis do not need it.
    if name in __all__:
        from lab.learner import window

        return getattr(window, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
