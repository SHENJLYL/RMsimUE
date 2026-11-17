"""Compatibility descriptor for the pinned exporter on modern Python."""
class classproperty:
    def __init__(self, getter):
        self.getter = getter
    def __get__(self, instance, owner):
        return self.getter(owner)
    @property
    def __isabstractmethod__(self):
        return getattr(self.getter, '__isabstractmethod__', False)
