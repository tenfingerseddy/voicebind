import QtQuick
import QtQuick.Controls
import qs.Commons
TextField {
    id: field
    renderType: TextInput.NativeRendering
    implicitHeight: Style.space(38)
    padding: Style.space(10)
    color: Color.popups.text
    placeholderTextColor: Color.muted
    selectionColor: Color.accent
    selectedTextColor: Color.background
    font.family: Style.font.family
    font.pixelSize: Style.font.body
    selectByMouse: true
    background: Rectangle {
        color: "transparent"
        border.color: field.activeFocus ? Color.accent : Color.popups.border
        radius: Style.space(4)
    }
}
