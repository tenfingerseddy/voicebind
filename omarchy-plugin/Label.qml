import QtQuick
import qs.Commons
Text {
    readonly property color secondaryColor: Qt.rgba(Color.popups.text.r, Color.popups.text.g, Color.popups.text.b, 0.68)
    renderType: Text.NativeRendering
    textFormat: Text.PlainText
    color: Color.popups.text
    font.family: Style.font.family
    font.pixelSize: Style.font.body
    wrapMode: Text.WordWrap
}
