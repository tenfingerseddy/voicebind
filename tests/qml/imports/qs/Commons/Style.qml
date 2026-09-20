pragma Singleton
import QtQuick
QtObject {
    readonly property var font: ({family: "monospace", body: 12, heading: 16})
    function space(n) { return n }
}
