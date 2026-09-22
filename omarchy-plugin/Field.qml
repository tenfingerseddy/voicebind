import QtQuick
import QtQuick.Controls
import qs.Commons
TextField {
    id: field
    renderType: TextInput.NativeRendering
    implicitHeight: Style.space(34)
    padding: Style.space(10)
    color: Color.popups.text
    placeholderTextColor: Qt.rgba(Color.popups.text.r, Color.popups.text.g, Color.popups.text.b, 0.55)
    selectionColor: Color.accent
    selectedTextColor: Color.background
    font.family: Style.font.family
    font.pixelSize: Style.font.body
    selectByMouse: true
    opacity: enabled ? 1 : 0.4
    background: Rectangle {
        color: Qt.rgba(Color.popups.text.r, Color.popups.text.g, Color.popups.text.b, field.activeFocus ? 0.055 : 0.025)
        border.color: field.activeFocus ? Color.accent : Qt.rgba(Color.popups.text.r, Color.popups.text.g, Color.popups.text.b, field.hovered ? 0.4 : 0.22)
        radius: Math.min(Style.space(4), Style.cornerRadius || 0)
    }
}
