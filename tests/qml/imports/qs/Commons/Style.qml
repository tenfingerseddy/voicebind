pragma Singleton
import QtQuick
QtObject {
    readonly property var font: ({family: "monospace", caption: 10, body: 12, heading: 16})
    readonly property int cornerRadius: 0
    function space(n) { return n }
}
