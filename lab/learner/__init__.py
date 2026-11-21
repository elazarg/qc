"""The Guitar Learner: tag dry recordings with the Charvel's pickup, mini-toggle and knobs, and have it
tell them from the sound alone.

    from lab.learner import GuitarLearner
    learner = GuitarLearner(seat)     # seat: see lab.learner.seat.LearnerSeat
    learner.show()

    calibration    guitar input calibration, one control at a time: calibration.open(seat)
    gestures       a return gesture on a guitar control turns a Shape knob: gestures.start(seat), gestures.stop()
    compensation   whether a high shelf can make up for a rolled-back tone pot
    board          the guitar's controls, drawn on a photograph of it
    window         the window itself
    dataset        labelled excerpts of dry takes, per guitar and layout
    configuration  fitting a profile to the examples, and estimating a setup from a clip
    state          pitch and harmonic readings of mono dry audio, and one-control profiles
    continuous     following live playing, and noticing a change of setup on a held note
    live           the live listening loop, run inside the worker process
    worker         that process (live listening or gesture listening), and its supervisor on the desk's side
    takes          recording a take
    inputs         the unit's ASIO inputs and its USB dry/wet routing
    folders        where everything is kept
"""

__all__ = ["GuitarLearner"]


def __getattr__(name):
    # The window brings in Tk; the worker process and the analysis do not need it.
    if name == "GuitarLearner":
        from lab.learner.window import GuitarLearner

        return GuitarLearner
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
