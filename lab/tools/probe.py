"""The smallest side-tool operation: what firmware the unit runs. Also the example for writing one."""


def firmware(engine, *, stop=None):
    return engine.unit.firmware


def scene(engine, *, stop=None):
    """The scene the unit is on."""
    return engine.unit.scene()


def held(engine, *, stop=None):
    """The chain the unit holds now, as a record."""
    return engine.unit.read().to_record()
