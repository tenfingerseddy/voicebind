import QtQuick
import QtQuick.Controls as Controls
import qs.Commons

Controls.Button {
    id: tab
    property bool selected: false
    property bool secondary: false
    implicitHeight: Style.space(secondary ? 28 : 36)
    implicitWidth: caption.implicitWidth + Style.space(secondary ? 20 : 12)
    padding: Style.space(6)
    Accessible.role: Accessible.PageTab
    Accessible.name: text
    Accessible.selected: selected
    contentItem: Label {
        id: caption
        text: tab.text
        color: tab.selected ? Color.popups.text : secondaryColor
        font.bold: tab.selected
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        wrapMode: Text.NoWrap
    }
    background: Rectangle {
        color: tab.secondary && tab.selected ? Qt.rgba(Color.popups.text.r, Color.popups.text.g, Color.popups.text.b, 0.09) : tab.hovered ? Qt.rgba(Color.popups.text.r, Color.popups.text.g, Color.popups.text.b, 0.05) : "transparent"
        radius: Math.min(Style.space(4), Style.cornerRadius || 0)
        border.width: tab.activeFocus ? 1 : 0
        border.color: Color.accent
        Rectangle {
            anchors { left: parent.left; right: parent.right; bottom: parent.bottom }
            height: 2
            visible: tab.selected && !tab.secondary
            color: Color.accent
        }
    }
}
