import QtQuick
import QtQuick.Layouts
import qs.Commons

Item {
    id: root
    required property var backend
    property bool issuesOnly: false
    property var selected: null
    readonly property var rows: backend.history.filter(function(row) { return !root.issuesOnly || row.issue })
    onIssuesOnlyChanged: pager.page = 0
    function details(row) {
        var parts = []
        if (row.route) parts.push("Interpretation: " + (row.route === "jev" ? "Jev" : row.route === "local" ? "Local" : row.route))
        if (row.activation) parts.push("Input: " + (row.activation === "ptt" ? "F10" : row.activation === "wake-followup" ? "After wake phrase" : "Wake phrase"))
        var names = {release_to_result: "Total from F10 release", end_of_speech_to_result: "Total from end of speech", whisper: "Speech recognition", decision_and_action: "Interpret + execute", jev: "Jev (included in interpretation)"}
        Object.keys(names).forEach(function(k) {
            if (row.ms[k] !== undefined) parts.push(names[k] + ": " + Math.round(row.ms[k]) + " ms")
        })
        if (row.ms.end_of_speech_to_result !== undefined) parts.push("Total includes silence wait, recognition and execution.")
        else if (row.ms.release_to_result !== undefined) parts.push("Total includes recognition and execution. No silence wait.")
        return parts.join("\n")
    }
    ColumnLayout {
        anchors.fill: parent
        visible: root.selected === null
        spacing: Style.space(10)
        RowLayout {
            Label { Layout.fillWidth: true; text: "RECENT ACTIVITY"; font.bold: true; font.pixelSize: Style.font.caption || Style.font.body; color: secondaryColor }
            Tab { text: "All"; secondary: true; selected: !root.issuesOnly; onClicked: root.issuesOnly = false }
            Tab { text: "Issues"; secondary: true; selected: root.issuesOnly; onClicked: root.issuesOnly = true }
            Button { text: "Refresh"; quiet: true; enabled: !backend.busy; onClicked: backend.refreshHistory() }
        }
        Label { Layout.fillWidth: true; text: "Select a command for its result and timing. Dictation text stays out of history."; color: secondaryColor; font.pixelSize: Style.font.caption || Style.font.body }
        Item {
            id: entries
            Layout.fillWidth: true; Layout.fillHeight: true
            ColumnLayout {
                anchors { left: parent.left; right: parent.right; top: parent.top }
                spacing: Style.space(8)
                Label {
                    Layout.fillWidth: true
                    visible: backend.historyLoaded && root.rows.length === 0
                    text: root.issuesOnly ? "No issues in recent history." : "No voice activity recorded yet."
                }
                Repeater {
                    model: root.rows.slice(pager.first, pager.end)
                    Button {
                        required property var modelData
                        Layout.fillWidth: true
                        implicitWidth: 0
                        implicitHeight: Style.space(74)
                        quiet: true
                        text: modelData.heard || modelData.title
                        onClicked: root.selected = modelData
                        contentItem: ColumnLayout {
                            spacing: Style.space(4)
                            RowLayout {
                                Label { Layout.fillWidth: true; text: modelData.heard || modelData.title; font.bold: true; maximumLineCount: 1; elide: Text.ElideRight }
                                Label { text: "›"; color: secondaryColor }
                            }
                            RowLayout {
                                Label { text: modelData.outcome; color: modelData.issue ? Color.urgent : Color.accent; font.pixelSize: Style.font.caption || Style.font.body }
                                Label { Layout.fillWidth: true; text: modelData.route === "jev" ? "· Jev" : modelData.route === "local" ? "· Local" : ""; color: secondaryColor; font.pixelSize: Style.font.caption || Style.font.body }
                                Label { text: Qt.formatDateTime(new Date(modelData.t * 1000), "ddd · HH:mm"); color: secondaryColor; font.pixelSize: Style.font.caption || Style.font.body }
                            }
                        }
                        Divider { anchors { left: parent.left; right: parent.right; bottom: parent.bottom } }
                    }
                }
            }
        }
        Pager { id: pager; objectName: "historyPager"; Layout.fillWidth: true; count: root.rows.length; pageSize: Math.max(1, Math.floor((entries.height + Style.space(8)) / Style.space(82))); unit: "entries" }
    }
    ColumnLayout {
        anchors.fill: parent
        visible: root.selected !== null
        spacing: Style.space(10)
        RowLayout {
            Button { text: "‹ History"; onClicked: root.selected = null }
            Label { Layout.fillWidth: true; text: root.selected ? Qt.formatDateTime(new Date(root.selected.t * 1000), "ddd d MMM · HH:mm:ss") : ""; color: secondaryColor; horizontalAlignment: Text.AlignRight }
        }
        TextPages {
            Layout.fillWidth: true; Layout.fillHeight: true
            text: root.selected ? [root.selected.outcome,
                root.selected.heard ? "Heard: “" + root.selected.heard + "”" : root.selected.title,
                root.selected.plan, root.selected.message, root.details(root.selected)].filter(function(s) { return !!s }).join("\n\n") : ""
        }
    }
}
