import QtQuick
import QtQuick.Controls as Controls
import QtQuick.Layouts
import qs.Commons

// Model-owned state: callers update their draft on clicked, just like a button.
Controls.Button {
    id: toggle
    property bool checkedValue: false
    property string description: ""
    implicitHeight: Math.max(Style.space(42), labels.implicitHeight + Style.space(8))
    implicitWidth: Style.space(240)
    padding: Style.space(4)
    Accessible.role: Accessible.CheckBox
    Accessible.name: text
    Accessible.description: description
    Accessible.checkable: true
    Accessible.checked: checkedValue
    contentItem: RowLayout {
        spacing: Style.space(16)
        ColumnLayout {
            id: labels
            Layout.fillWidth: true
            spacing: Style.space(2)
            Label { Layout.fillWidth: true; text: toggle.text; font.bold: true }
            Label { Layout.fillWidth: true; visible: text !== ""; text: toggle.description; color: secondaryColor; font.pixelSize: Style.font.caption || Style.font.body }
        }
        Rectangle {
            Layout.preferredWidth: Style.space(36)
            Layout.preferredHeight: Style.space(20)
            color: Qt.rgba(Color.popups.text.r, Color.popups.text.g, Color.popups.text.b, toggle.checkedValue ? 0.2 : 0.07)
            border.width: toggle.checkedValue ? 0 : 1
            border.color: Qt.rgba(Color.popups.text.r, Color.popups.text.g, Color.popups.text.b, 0.3)
            radius: Style.cornerRadius > 0 ? height / 2 : 0
            Rectangle {
                width: Style.space(14); height: width
                x: toggle.checkedValue ? parent.width - width - Style.space(3) : Style.space(3)
                anchors.verticalCenter: parent.verticalCenter
                color: toggle.checkedValue ? Color.popups.text : Qt.rgba(Color.popups.text.r, Color.popups.text.g, Color.popups.text.b, 0.5)
                radius: parent.radius > 0 ? height / 2 : 0
                Behavior on x { NumberAnimation { duration: 120; easing.type: Easing.OutCubic } }
            }
        }
    }
    background: Rectangle {
        color: toggle.hovered ? Qt.rgba(Color.popups.text.r, Color.popups.text.g, Color.popups.text.b, 0.04) : "transparent"
        border.width: toggle.activeFocus ? 1 : 0
        border.color: Color.accent
        radius: Math.min(Style.space(4), Style.cornerRadius || 0)
    }
}
