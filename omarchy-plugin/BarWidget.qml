import QtQuick
import Quickshell.Io
import qs.Commons
import qs.Ui as Ui

Ui.BarWidget {
    id: root
    moduleName: "io.github.tenfingerseddy.voicebind"
    readonly property bool opened: panel.opened
    readonly property bool popoutSwitchClosing: panel.popoutSwitchClosing
    implicitWidth: button.implicitWidth
    implicitHeight: button.implicitHeight
    function open() { panel.open() }
    function close() { panel.close() }
    function closeForPopoutSwitch() { panel.closeForPopoutSwitch() }
    function navigate(name) {
        if (!contents.item) return
        if (["Voice", "Phrases", "Apps", "Bookmarks", "History"].indexOf(name) >= 0) contents.item.page = name
        else if (["Listening", "Appearance", "Jev"].indexOf(name) >= 0) { contents.item.page = "Voice"; contents.item.voicePage = name }
    }
    function panelState() {
        return JSON.stringify({open: opened, online: backend.online, phase: backend.status.phase,
            loaded: contents.item !== null && backend.document !== null, busy: backend.busy, error: backend.failed ? backend.notice : "",
            historyLoaded: backend.historyLoaded, historyCount: backend.history.length,
            page: contents.item ? contents.item.page : "", color: String(Color.popups.background),
            scale: popup.devicePixelRatio,
            rect: popup.screen ? {x: popup.screen.x + popup.cardOrigin.x, y: popup.screen.y + popup.cardOrigin.y,
                width: popup.contentWidth, height: popup.contentHeight} : null})
    }
    Backend { id: backend }
    IpcHandler {
        target: "jev-voice-bar"
        function state(): string {
            var items = root.bar ? root.bar.moduleWidgets(root.moduleName) : [root]
            return JSON.stringify(items.map(function(item) { return JSON.parse(item.panelState()) }))
        }
        function page(name: string): void {
            var items = root.bar ? root.bar.moduleWidgets(root.moduleName) : [root]
            items.forEach(function(item) { item.navigate(name) })
        }
    }
    Ui.BarIconButton {
        id: button
        anchors.fill: parent
        bar: root.bar
        active: root.opened
        opticalSize: Math.max(Style.space(18), Style.bar.iconCanvas)
        tooltipText: "Voicebind · " + (!backend.online ? "Stopped" : !backend.status.wake_enabled ? "Wake paused" : "Ready")
        iconComponent: Component { Icon { anchors.fill: parent; foreground: button.foreground; paused: !backend.online || !backend.status.wake_enabled } }
        onPressed: function(mouseButton) { if (mouseButton === Qt.LeftButton) panel.toggle() }
    }
    Ui.Panel {
        id: panel
        bar: root.bar
        moduleName: root.moduleName
        manageIpc: false
        onOpenedChanged: if (opened) backend.request({action: "panel"})
        Ui.KeyboardPanel {
            id: popup
            anchorItem: button
            bar: root.bar
            owner: root
            open: panel.opened
            popoutSwitching: panel.popoutSwitching
            popoutSwitchClosing: panel.popoutSwitchClosing
            focusTarget: contents.item
            contentWidth: fittedContentWidth(Style.space(500))
            contentHeight: fittedContentHeight(Style.space(560))
            Loader {
                id: contents
                anchors.fill: parent
                active: panel.opened
                onActiveChanged: {
                    if (active) setSource(Qt.resolvedUrl("PanelContent.qml"), {backend: backend})
                    else source = ""
                }
                onLoaded: item.closeRequested.connect(root.close)
            }
        }
    }
}
