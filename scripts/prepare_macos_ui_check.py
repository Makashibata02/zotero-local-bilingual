"""Prepare an isolated Zotero profile for native Mac UI checks; never launch it.

Build the plugin first. Then launch Zotero with the printed profile path and
`-no-remote -headless`. The fixture uses generated metadata and mock responses;
it closes only its own Zotero process after writing output/native-ui/results.json.
"""
from pathlib import Path
import json
import shutil
import zipfile


def main():
    root = Path(__file__).resolve().parents[1]
    base = root / "output/native-ui"
    extension = base / "extension"
    (extension / "content").mkdir(parents=True, exist_ok=True)
    (base / "profile/extensions").mkdir(parents=True, exist_ok=True)
    (base / "data").mkdir(exist_ok=True)
    manifest = json.loads((root / "plugin/addon/manifest.json").read_text())
    version = manifest["version"]
    manifest.update(name="Isolated Mac UI verification", version=version + ".12")
    manifest.pop("icons", None)
    manifest["applications"]["zotero"]["id"] = "zlb-ui-test@local.invalid"
    (extension / "manifest.json").write_text(json.dumps(manifest))
    for name in ("confirm.xhtml", "workbench.xhtml", "macos-menus.css"):
        shutil.copyfile(root / "plugin/addon/content" / name, extension / "content" / name)
    shutil.copyfile(root / "plugin/build/content/scripts/local-bilingual.js", extension / "content/plugin.js")
    # Match the preferences host's real platform/theme styles, without invoking
    # its service-loading onload handler or looking up a user's preferences.
    fragment = (root / "plugin/addon/content/preferences.xhtml").read_text().replace(
        ' onload="Zotero.LocalBilingual.preferences.onLoad(window.document)"', ""
    )
    styles = ["global/skin/global.css", "zotero-platform/content/preferences.css",
              "zotero/skin/preferences.css", "zotero/skin/zotero.css",
              "zotero-platform/content/overlay.css", "zotero-platform-version/content/style.css"]
    settings = '<?xml version="1.0"?>\n' + "\n".join(
        f'<?xml-stylesheet href="chrome://{style}"?>' for style in styles
    ) + '\n<window id="zotero-prefs" xmlns="http://www.mozilla.org/keymaster/gatekeeper/there.is.only.xul" style="overflow:auto;">' + fragment + "</window>"
    (extension / "content/settings.xhtml").write_text(settings)
    preferences = {
        "extensions.autoDisableScopes": 0, "extensions.enabledScopes": 15,
        "extensions.startupScanScopes": 15, "extensions.zotero.useDataDir": True,
        "extensions.zotero.dataDir": str(base / "data"),
        "extensions.zotero.firstRun.skipFirefoxProfileAccessCheck": True,
        "extensions.zotero.sync.autoSync": False, "app.update.enabled": False,
        "extensions.update.enabled": False, "extensions.logging.enabled": True,
        "browser.dom.window.dump.enabled": True,
    }
    (base / "profile/user.js").write_text("\n".join(
        f"user_pref({json.dumps(key)},{json.dumps(value)});" for key, value in preferences.items()
    ))
    source = (root / "plugin/tests/macos-native-bootstrap.js").read_text()
    (extension / "bootstrap.js").write_text(source.replace("__BASE__", json.dumps(str(base))).replace("__VERSION__", version))
    with zipfile.ZipFile(base / "profile/extensions/zlb-ui-test@local.invalid.xpi", "w", zipfile.ZIP_DEFLATED) as archive:
        for path in extension.rglob("*"):
            if path.is_file():
                archive.write(path, path.relative_to(extension).as_posix())
    print(base / "profile")


if __name__ == "__main__":
    main()
