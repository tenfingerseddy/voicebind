import QtQuick
import QtQuick.Controls as Controls
import qs.Commons
Controls.Button {
    id: button
    property bool primary: false
    implicitHeight: Style.space(36)
    implicitWidth: caption.implicitWidth + Style.space(24)
    padding: Style.space(8)
    Accessible.name: text
    contentItem: Label {
        id: caption
        text: button.text
        color: button.primary ? Color.background : Color.popups.text
        opacity: button.enabled ? 1 : 0.4
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        wrapMode: Text.NoWrap
        elide: Text.ElideRight
    }
    background: Rectangle {
        color: button.primary ? Color.accent : button.hovered ? Qt.rgba(Color.accent.r, Color.accent.g, Color.accent.b, 0.15) : "transparent"
        border.color: button.activeFocus ? Color.accent : Color.popups.border
        radius: Style.space(4)
        opacity: button.enabled ? 1 : 0.4
    }
}
