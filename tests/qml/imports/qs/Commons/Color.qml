pragma Singleton
import QtQuick
QtObject {
    readonly property color accent: "#ffad66"
    readonly property color background: "#00172e"
    readonly property color muted: "#709090"
    readonly property color urgent: "#f44"
    readonly property QtObject popups: QtObject {
        readonly property color background: "#00172e"
        readonly property color text: "#ffddaa"
        readonly property color border: "#ffad66"
    }
}
