"""The Guitar Learner: tag dry recordings with the Charvel's pickup, mini-toggle and knobs, and have it
tell them from the sound alone.

    from lab.learner import GuitarLearner
    learner = GuitarLearner(seat)     # seat: see lab.desk.LearnerSeat
    learner.show()

    board          the guitar's controls, drawn on a photograph of it
    window         the window itself
    dataset        labelled excerpts of dry takes, per guitar and layout
    configuration  fitting a profile to the examples, and estimating a setup from a clip
    state          pitch and harmonic readings of mono dry audio, and one-control profiles
    continuous     following live playing, and noticing a change of setup on a held note
    live           the live listening loop, run inside the worker process
    worker         that process, and its supervisor on the desk's side
    takes          recording a take
    inputs         the unit's ASIO inputs and its USB dry/wet routing
    folders        where everything is kept
"""

__all__ = ["GuitarLearner"]


def __getattr__(name):
    # The window brings in Tk; the worker process and the analysis do not need it.
    if name == "GuitarLearner":
        from .window import GuitarLearner

        return GuitarLearner
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
