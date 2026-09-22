import QtQuick
import QtQuick.Controls as Controls
import qs.Commons
Controls.Button {
    id: button
    property bool primary: false
    property bool quiet: false
    implicitHeight: Style.space(32)
    implicitWidth: caption.implicitWidth + Style.space(24)
    padding: Style.space(8)
    Accessible.name: text
    contentItem: Label {
        id: caption
        text: button.text
        color: button.primary && button.enabled ? Color.background : Color.popups.text
        opacity: button.enabled ? 1 : 0.4
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        wrapMode: Text.NoWrap
        elide: Text.ElideRight
    }
    background: Rectangle {
        color: button.primary && button.enabled ? Color.accent : button.down ? Qt.rgba(Color.accent.r, Color.accent.g, Color.accent.b, 0.18) : button.hovered ? Qt.rgba(Color.popups.text.r, Color.popups.text.g, Color.popups.text.b, 0.08) : "transparent"
        border.color: button.activeFocus ? Color.accent : Qt.rgba(Color.popups.text.r, Color.popups.text.g, Color.popups.text.b, 0.25)
        border.width: button.quiet && !button.activeFocus ? 0 : 1
        radius: Math.min(Style.space(4), Style.cornerRadius || 0)
        opacity: button.enabled ? 1 : 0.4
        Behavior on color { ColorAnimation { duration: 100 } }
    }
}
