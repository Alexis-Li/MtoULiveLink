"""Qt controls and resource observations shared by the Maya GUI checks."""

MAIN = "MtoULiveLinkWindow"
DIAGNOSTICS = "MtoULiveLinkDiagnosticWindow"
CONNECTION, PREVIEW, CAMERA, DIAGNOSTIC_PAGE = range(4)


def windows(name, visible_only=True):
    from mtou_livelink.qt_compat import QtWidgets

    return [window for window in QtWidgets.QApplication.topLevelWidgets()
            if window.objectName() == name and (window.isVisible() or not visible_only)]


def control(parent, name):
    from mtou_livelink.qt_compat import QtWidgets

    widget = parent.findChild(QtWidgets.QWidget, name)
    if widget is None:
        raise AssertionError("missing Qt control " + name)
    return widget


def page(panel, index):
    tabs = control(panel, "panelTabs")
    tabs.setCurrentIndex(index)
    if tabs.currentIndex() != index:
        raise AssertionError(f"could not show panel tab {index}")


def click(panel, name):
    button = control(panel, name)
    if not button.isVisible() or not button.isEnabled():
        raise AssertionError(f"{name} is unavailable: {button.toolTip()}")
    button.click()


def set_checked(panel, name, value):
    if control(panel, name).isChecked() != value:
        click(panel, name)
    assert control(panel, name).isChecked() == value, name


def set_capture_range(panel, start, end):
    page(panel, PREVIEW)
    set_checked(panel, "rangeCustom", True)
    start_field = control(panel, "rangeStart")
    fields = ((start_field, int(start)), (control(panel, "rangeEnd"), int(end)))
    if end >= start_field.value():
        fields = tuple(reversed(fields))
    for field, value in fields:
        field.setValue(value)
        field.editingFinished.emit()


def save_image(widget, path):
    shot = widget.grab()
    if shot.isNull() or not shot.save(str(path)):
        raise RuntimeError(f"could not save screenshot {path}")
    return {"window": widget.objectName(), "path": str(path),
            "size": [widget.width(), widget.height()]}


class CallbackCensus:
    """Observe the tools' Maya callback IDs at registration and removal."""

    _families = ("MSceneMessage", "MTimerMessage", "MEventMessage", "MNodeMessage",
                 "MDGMessage", "MMessage")

    def __init__(self, open_maya):
        self._open_maya = open_maya
        self.live = {}

    @classmethod
    def install(cls):
        from mtou_livelink import mayaapi

        if not isinstance(mayaapi.om, cls):
            mayaapi.om = cls(mayaapi.om)
        return mayaapi.om

    def __getattr__(self, name):
        family = getattr(self._open_maya, name)
        return _CountedMessages(family, name, self.live) if name in self._families else family


class _CountedMessages:
    def __init__(self, real, family, live):
        self._real = real
        self._family = family
        self._live = live

    def __getattr__(self, name):
        method = getattr(self._real, name)
        if name.startswith("add") and callable(method):
            def add(*args, **kwargs):
                callback_id = method(*args, **kwargs)
                self._live[callback_id] = self._family
                return callback_id

            return add
        if name in ("removeCallback", "removeCallbacks") and callable(method):
            def remove(ids, *args, **kwargs):
                result = method(ids, *args, **kwargs)
                for callback_id in (ids if name == "removeCallbacks" else [ids]):
                    self._live.pop(callback_id, None)
                return result

            return remove
        return method
