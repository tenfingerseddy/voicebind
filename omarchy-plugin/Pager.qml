import QtQuick
import QtQuick.Layouts
import qs.Commons

RowLayout {
    id: root
    property int count: 0
    property int pageSize: 1
    property int page: 0
    readonly property int pageCount: Math.max(1, Math.ceil(count / Math.max(1, pageSize)))
    readonly property int first: page * Math.max(1, pageSize)
    readonly property int end: Math.min(count, first + Math.max(1, pageSize))
    property string unit: ""
    onPageCountChanged: page = Math.min(page, pageCount - 1)
    function showLast() { page = pageCount - 1 }
    spacing: Style.space(8)
    Button { text: "‹"; Accessible.name: "Previous page"; enabled: root.page > 0; onClicked: root.page-- }
    Label {
        Layout.fillWidth: true
        horizontalAlignment: Text.AlignHCenter
        text: root.count ? (root.first + 1) + (root.end > root.first + 1 ? "–" + root.end : "") + " of " + root.count + (root.unit ? " " + root.unit : "") : "No entries"
        color: Color.muted
    }
    Button { text: "›"; Accessible.name: "Next page"; enabled: root.page + 1 < root.pageCount; onClicked: root.page++ }
}
