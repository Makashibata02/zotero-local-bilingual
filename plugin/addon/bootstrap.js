/* global Components, Services, Zotero, APP_SHUTDOWN */

var chromeHandle;

function install() {}

function reportStartupError(error) {
  try {
    Zotero.logError(error);
  } catch (_error) {}
  try {
    Components.utils.reportError(error);
  } catch (_error) {}
}

async function startup({ rootURI }) {
  try {
    const startupService = Components.classes[
      "@mozilla.org/addons/addon-manager-startup;1"
    ].getService(Components.interfaces.amIAddonManagerStartup);
    const manifestURI = Services.io.newURI(rootURI + "manifest.json");
    chromeHandle = startupService.registerChrome(manifestURI, [
      ["content", "local-bilingual", rootURI + "content/"]
    ]);
    const context = { rootURI, Zotero, Components, Services, ChromeUtils };
    for (const name of ["IOUtils", "PathUtils", "Headers", "FormData", "Blob", "crypto", "AbortSignal", "TextEncoder"]) {
      Object.defineProperty(context, name, {
        get: () => Zotero.getMainWindow()[name]
      });
    }
    context.fetch = (...args) => Zotero.getMainWindow().fetch(...args);
    Services.scriptloader.loadSubScript(rootURI + "runtime-native.js", context);
    context.RuntimeNative = context.createRuntimeNative(context);
    context._globalThis = context;
    Services.scriptloader.loadSubScript(
      rootURI + "content/scripts/local-bilingual.js",
      context
    );
    // Do not await a hook that waits for Zotero.initializationPromise here.
    // Zotero invokes bootstrap startup as part of its own initialization.
    void Zotero.LocalBilingual.hooks.onStartup().catch(reportStartupError);
  } catch (error) {
    reportStartupError(error);
    throw error;
  }
}

async function onMainWindowLoad({ window }) {
  await Zotero.LocalBilingual?.hooks.onMainWindowLoad(window);
}

async function onMainWindowUnload({ window }) {
  await Zotero.LocalBilingual?.hooks.onMainWindowUnload(window);
}

function shutdown(_data, reason) {
  if (reason === APP_SHUTDOWN) return;
  Zotero.LocalBilingual?.hooks.onShutdown();
  if (chromeHandle) {
    chromeHandle.destruct();
    chromeHandle = null;
  }
}

function uninstall() {}
