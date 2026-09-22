import QtQuick
import QtQuick.Controls as Controls
import QtQuick.Layouts
import qs.Commons
Controls.ComboBox {
    id: select
    implicitHeight: Style.space(34)
    font.family: Style.font.family
    font.pixelSize: Style.font.body
    leftPadding: Style.space(10)
    rightPadding: Style.space(28)
    palette.window: Color.popups.background
    palette.base: Color.popups.background
    palette.text: Color.popups.text
    palette.buttonText: Color.popups.text
    palette.highlight: Color.accent
    palette.highlightedText: Color.background
    contentItem: Label { text: select.displayText; verticalAlignment: Text.AlignVCenter; wrapMode: Text.NoWrap; elide: Text.ElideRight }
    background: Rectangle { color: Qt.rgba(Color.popups.text.r, Color.popups.text.g, Color.popups.text.b, 0.025); border.color: select.activeFocus ? Color.accent : Qt.rgba(Color.popups.text.r, Color.popups.text.g, Color.popups.text.b, select.hovered ? 0.4 : 0.22); radius: Math.min(Style.space(4), Style.cornerRadius || 0) }
    indicator: Label { text: "⌄"; x: select.width - width - Style.space(10); anchors.verticalCenter: parent.verticalCenter }
    // Long app/microphone menus use search and pages rather than a scroll list.
    popup: Controls.Popup {
        id: choices
        y: select.height + Style.space(4)
        width: select.width
        padding: Style.space(8)
        implicitHeight: choiceColumn.implicitHeight + padding * 2
        closePolicy: Controls.Popup.CloseOnEscape | Controls.Popup.CloseOnPressOutside
        property var matches: {
            var result = [], query = search.text.trim().toLowerCase(), values = select.model || []
            for (var i = 0; i < values.length; i++) {
                var name = String(values[i][select.textRole] || "")
                if (!query || name.toLowerCase().indexOf(query) >= 0) result.push({name: name, sourceIndex: i})
            }
            return result
        }
        function choose(index) {
            select.currentIndex = index
            select.activated(index)
            close()
            select.forceActiveFocus()
        }
        onOpened: { search.text = ""; choicePager.page = 0; search.forceActiveFocus() }
        background: Rectangle { color: Color.popups.background; border.color: Qt.rgba(Color.popups.text.r, Color.popups.text.g, Color.popups.text.b, 0.3); radius: Math.min(Style.space(4), Style.cornerRadius || 0) }
        contentItem: ColumnLayout {
            id: choiceColumn
            spacing: Style.space(6)
            Field {
                id: search
                Layout.fillWidth: true
                placeholderText: "Search…"
                onTextChanged: choicePager.page = 0
                onAccepted: if (choices.matches.length) choices.choose(choices.matches[choicePager.first].sourceIndex)
            }
            Repeater {
                model: choices.matches.slice(choicePager.first, choicePager.end)
                Button { required property var modelData; Layout.fillWidth: true; primary: select.currentIndex === modelData.sourceIndex; text: modelData.name; onClicked: choices.choose(modelData.sourceIndex) }
            }
            Label { visible: choices.matches.length === 0; text: "No matches"; color: secondaryColor }
            Pager { id: choicePager; Layout.fillWidth: true; count: choices.matches.length; pageSize: 4 }
        }
    }
}
