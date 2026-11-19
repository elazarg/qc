"""The smallest side-tool operation: what firmware the unit runs. Also the example for writing one."""


def firmware(engine, *, stop=None):
    return engine.unit.firmware
