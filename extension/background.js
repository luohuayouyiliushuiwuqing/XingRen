/* service worker：只在安装时补写默认看板地址（storage 被清空而没重装时，
   popup 的读取路径也有 DEFAULT_BOARD_URL 兜底）。它的存在还让 Playwright
   可以用 context.serviceWorkers() 拿到扩展做自动化。 */
importScripts("defaults.js");

chrome.runtime.onInstalled.addListener(() => {
  chrome.storage.local.get("boardUrl").then((data) => {
    if (!data.boardUrl) chrome.storage.local.set({ boardUrl: DEFAULT_BOARD_URL });
  });
});
