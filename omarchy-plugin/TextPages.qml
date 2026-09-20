import QtQuick
import qs.Commons

Item {
    id: root
    property string text: ""
    property var chunks: []
    // Measure real wrapped text, so long diagnostics remain fully readable
    // without a scroll area, truncation or a character-count guess.
    function paginate() {
        if (body.width < 1 || body.height < Style.font.body * 2) return
        var remaining = text, pages = []
        while (remaining.length) {
            var low = 1, high = remaining.length, fit = 1
            while (low <= high) {
                var mid = Math.floor((low + high) / 2)
                measure.text = remaining.slice(0, mid)
                measure.forceLayout()
                if (measure.implicitHeight <= body.height) { fit = mid; low = mid + 1 }
                else high = mid - 1
            }
            if (fit < remaining.length) {
                var space = Math.max(remaining.lastIndexOf(" ", fit - 1), remaining.lastIndexOf("\n", fit - 1))
                if (space > fit / 2) fit = space + 1
            }
            pages.push(remaining.slice(0, fit))
            remaining = remaining.slice(fit)
        }
        chunks = pages
        pager.page = 0
    }
    onTextChanged: repaginate.restart()
    Timer { id: repaginate; interval: 0; onTriggered: root.paginate() }
    Label {
        id: body
        anchors { left: parent.left; right: parent.right; top: parent.top; bottom: pager.top; bottomMargin: Style.space(8) }
        text: root.chunks[pager.page] || ""
        wrapMode: Text.Wrap
        onWidthChanged: repaginate.restart()
        onHeightChanged: repaginate.restart()
    }
    Label { id: measure; visible: false; width: body.width; wrapMode: Text.Wrap }
    Pager { id: pager; anchors { left: parent.left; right: parent.right; bottom: parent.bottom } count: root.chunks.length; unit: "pages" }
}
