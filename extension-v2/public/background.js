// ==================================================
// 🧠 BACKGROUND SCRIPT – AI ASSISTANT WITH TAB MANAGEMENT
// ==================================================

console.log("✅ Background script initialized");

// ==================================================
// 🗄️ GLOBAL QUEUES
// ==================================================
import { startAgentLoop, stopAgentLoop } from './features/agentManager.js';

// ==================================================
// 🗄️ GLOBAL QUEUES
// ==================================================
let pendingChatMessage = null;
let pendingExplanation = null;

// ==================================================
// 📨 MAIN MESSAGE LISTENER
// ==================================================
// ==================================================
// 📨 MAIN MESSAGE LISTENER
// ==================================================
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {

  // 1️⃣ OPEN SIDEBAR
  if (message.type === "OPEN_SIDEBAR") {
    const tabId = sender.tab?.id;
    if (tabId) {
      chrome.sidePanel.open({ tabId })
        .then(() => sendResponse({ success: true }))
        .catch(err => sendResponse({ success: false, error: err.message }));
    }
    return true;
  }

  // 2️⃣ SEND TO CHAT
  if (message.type === "SEND_TO_CHAT") {
    const tabId = sender.tab?.id;
    if (!tabId) return;

    pendingChatMessage = {
      text: message.message,
      url: message.meta?.url || sender.tab.url
    };

    chrome.sidePanel.open({ tabId }).then(() => {
      setTimeout(flushChatQueue, 50);
    });

    sendResponse({ success: true });
    return true;
  }

  // 3️⃣ SIDEBAR READY SIGNAL
  if (message.type === "SIDEBAR_READY") {
    flushChatQueue();
    flushExplanationQueue();
    sendResponse({ acknowledged: true });
    return true;
  }

  // 4️⃣ GET ALL TABS
  if (message.type === "GET_ALL_TABS") {
    getAllTabs(sendResponse, message);
    return true;
  }

  // 5️⃣ SWITCH TO TAB
  if (message.type === "SWITCH_TO_TAB") {
    switchToTab(message.tabId, sendResponse);
    return true;
  }

  // 6️⃣ CLOSE TAB
  if (message.type === "CLOSE_TAB") {
    closeTab(message.tabId, sendResponse);
    return true;
  }

  // 7️⃣ RECORDING HANDLERS
  if (message.type === "START_RECORDING" || message.type === "TOGGLE_SCREEN_RECORD") {
    // Basic toggle logic if TOGGLE_SCREEN_RECORD
    if (message.type === "TOGGLE_SCREEN_RECORD") {
      // We don't have a global isRecording in background easily without state
      // But we can check offscreen document
      chrome.runtime.getContexts({ contextTypes: ["OFFSCREEN_DOCUMENT"] }).then(contexts => {
        if (contexts.length > 0) stopRecording(sendResponse);
        else startRecording(sendResponse);
      });
    } else {
      startRecording(sendResponse);
    }
    return true;
  }

  if (message.type === "STOP_RECORDING") {
    stopRecording(sendResponse);
    return true;
  }

  // 🆕 8️⃣ RECORDING COMPLETE - Forward from offscreen to sidebar
  if (message.type === "RECORDING_COMPLETE") {
    console.log("✅ Recording complete in background, forwarding to sidebar...");
    console.log(`📹 Video size: ${message.video?.length || 0} characters`);

    // Forward to sidebar with RECORDING_DATA type
    chrome.runtime.sendMessage({
      type: "RECORDING_DATA",
      video: message.video
    }).then(() => {
      console.log("✅ Recording data sent to sidebar");
    }).catch(err => {
      console.error("❌ Failed to send to sidebar:", err);
      // If sidebar isn't ready, the message will be lost
      // You might want to store it temporarily
    });

    return true;
  }

  // 9️⃣ UTILITIES
  if (message.type === "OPEN_TAB") {
    chrome.tabs.create({ url: message.url });
    return true;
  }

  if (message.type === "TAKE_SCREENSHOT") {
    chrome.tabs.captureVisibleTab(null, { format: "png" }, (dataUrl) => {
      sendResponse({ success: !!dataUrl, image: dataUrl });
    });
    return true;
  }

  // 🔟 EXECUTE MANIFEST (Agent Mode)
  if (message.type === "EXECUTE_MANIFEST") {
    console.log("🚀 Executing manifest:", message.manifest.manifestId);
    executeManifest(message.manifest).then(result => {
      sendResponse({ success: true, result });
    }).catch(err => {
      console.error("❌ Manifest execution failed:", err);
      sendResponse({ success: false, error: err.message });
    });
    return true;
  }

  // 11️⃣ INIT EXECUTION (Streaming Mode)
  if (message.type === "INIT_EXECUTION") {
    executionContext = {
      results: {},
      manifest: message.manifest || { query: "Streaming Agent" },
      tabs: {}
    };
    broadcastStatus(`🚀 Starting: ${executionContext.manifest.query}`);
    sendResponse({ success: true });
    return true;
  }

  if (message.type === "UPDATE_CONTEXT") {
    if (executionContext.manifest) {
      Object.assign(executionContext.manifest, message.manifestUpdate);
      console.log("🔄 Background context updated:", message.manifestUpdate);
    }
    sendResponse({ success: true });
    return true;
  }

  // 12️⃣ EXECUTE STEP (Streaming Mode)
  if (message.type === "EXECUTE_STEP") {
    console.log(`▶ Received execution request for step ${message.step.id}`);

    (async () => {
      // Wait for dependencies if necessary
      if (message.step.dependencies && message.step.dependencies.length > 0) {
        const maxWait = 30000; // 30s timeout for dependencies
        const startTime = Date.now();

        while (true) {
          const missingDeps = message.step.dependencies.filter(depId => !executionContext.results[depId]);

          if (missingDeps.length === 0) break;

          if (Date.now() - startTime > maxWait) {
            const err = `Timeout waiting for dependencies: ${missingDeps.join(', ')}`;
            console.error(err);
            broadcastStatus(`❌ Step ${message.step.id} failed: Dependency timeout`);
            sendResponse({ success: false, error: err });
            return;
          }

          // Wait a bit before checking again
          await new Promise(r => setTimeout(r, 500));
        }
      }

      console.log(`▶ Executing step ${message.step.id}`);
      try {
        const result = await executeStep(message.step);
        if (executionContext.results) {
          executionContext.results[message.step.id] = result;
        }
        broadcastStatus(`✅ Step ${message.step.id} complete`);
        sendResponse({ success: true, result });
      } catch (err) {
        console.error(`❌ Step ${message.step.id} failed:`, err);
        broadcastStatus(`❌ Step ${message.step.id} failed: ${err.message}`);
        sendResponse({ success: false, error: err.message });
      }
    })();

    return true; // Keep channel open
  }

  // 🆕 13️⃣ CHECK GRAMMAR (AI-powered check)
  if (message.type === "CHECK_GRAMMAR") {
    const text = message.text;
    if (!text || text.length < 3) {
      sendResponse({ success: true, errors: [] });
      return true;
    }

    fetch("http://127.0.0.1:8000/grammar/check", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text })
    })
      .then(res => res.json())
      .then(data => {
        sendResponse({ success: true, errors: data.errors || [] });
      })
      .catch(err => {
        console.error("❌ Grammar check failed:", err);
        sendResponse({ success: false, error: err.message });
      });
    return true;
  }

  // 🆕 14️⃣ REWRITE TEXT
  if (message.type === "REWRITE_TEXT") {
    const { text, properties } = message;
    if (!text) {
      sendResponse({ success: false, error: "No text provided" });
      return true;
    }

    fetch("http://127.0.0.1:8000/text/rewrite", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, properties: properties || ["corrected", "professional", "concise"] })
    })
      .then(res => res.json())
      .then(data => {
        sendResponse({ success: true, versions: data.versions || {} });
      })
      .catch(err => {
        console.error("❌ Rewrite failed:", err);
        sendResponse({ success: false, error: err.message });
      });
    return true;
  }

  // 🆕 15️⃣ CUSTOMIZE DOM
  if (message.type === "CUSTOMIZE_DOM") {
    const { elements, requirements } = message;
    if (!elements || !requirements) {
      sendResponse({ success: false, error: "Missing elements or requirements" });
      return true;
    }

    fetch("http://127.0.0.1:8000/dom/customize", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ elements, requirements })
    })
      .then(res => res.json())
      .then(data => {
        // New format returns modifications array
        const modifications = data.modifications || data.elements || data.ELEMENTS || [];

        sendResponse({
          success: data.success !== false,
          modifications: modifications,
          error: data.error
        });
      })
      .catch(err => {
        console.error("❌ DOM customization failed:", err);
        sendResponse({ success: false, error: err.message });
      });
    return true;
  }

  // 🆕 16️⃣ GROUP TABS
  if (message.type === "GROUP_TABS") {
    analyzeAndGroupTabs(sendResponse);
    return true;
  }


  // 🆕 17️⃣ START AGENT LOOP (Tool-based)
  if (message.type === "START_AGENT_LOOP") {
    startAgentLoop(message.tabId, message.goal, message.history || [])
      .then(result => sendResponse(result))
      .catch(err => sendResponse({ success: false, error: err.message }));
    return true;
  }

  // 🆕 17.5️⃣ STOP AGENT LOOP
  if (message.type === "STOP_AGENT_LOOP") {
    stopAgentLoop();
    sendResponse({ success: true });
    return true;
  }

  // 🆕 18️⃣ CAPTURE VIEWPORT (for Circle to Search)
  if (message.type === "CAPTURE_VIEWPORT") {
    chrome.tabs.captureVisibleTab(null, { format: "png" }, (dataUrl) => {
      sendResponse({ success: !!dataUrl, image: dataUrl });
    });
    return true;
  }

  // 🆕 19️⃣ CIRCLE SEARCH COMPLETE (forward to sidebar)
  if (message.type === "CIRCLE_SEARCH_COMPLETE") {
    console.log("🔍 Circle search complete, forwarding to sidebar...");
    chrome.runtime.sendMessage({
      type: "CIRCLE_SEARCH_RESULT",
      imageData: message.imageData,
      pageUrl: message.pageUrl,
      pageTitle: message.pageTitle
    }).catch(err => {
      console.error("❌ Failed to forward to sidebar:", err);
    });
    sendResponse({ success: true });
    return true;
  }

  // 🆕 20️⃣ FETCH NEWS
  if (message.type === "FETCH_NEWS") {
    const NEWS_API_KEY = 'pub_81e9fbb1558843c1b88da57692d26e60';
    const country = message.country || 'in';
    const query = message.query || '';
    let url = `https://newsdata.io/api/1/latest?apikey=${NEWS_API_KEY}&country=${country}&language=en`;
    if (query) url += `&q=${encodeURIComponent(query)}`;

    fetch(url)
      .then(res => res.json())
      .then(data => sendResponse({ success: true, data }))
      .catch(err => sendResponse({ success: false, error: err.message }));
    return true;
  }

  // 🆕 21️⃣ TOGGLE DARK SITE
  if (message.type === "TOGGLE_DARK_SITE") {
    const tabId = sender.tab?.id || message.tabId;
    if (tabId) {
      toggleDarkSite(tabId).then(() => sendResponse({ success: true }));
    } else {
      chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
        if (tabs[0]) toggleDarkSite(tabs[0].id).then(() => sendResponse({ success: true }));
      });
    }
    return true;
  }

  // 🆕 22️⃣ TOGGLE CIRCLE SEARCH
  if (message.type === "TOGGLE_CIRCLE_SEARCH") {
    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
      if (tabs[0]) {
        chrome.tabs.sendMessage(tabs[0].id, { type: "ACTIVATE_CIRCLE_SEARCH" });
        sendResponse({ success: true });
      }
    });
    return true;
  }
});

// ==================================================
// 🧩 MANIFEST EXECUTOR ENGINE
// ==================================================
let executionContext = {};

async function executeManifest(manifest) {
  executionContext = {
    results: {},
    manifest: manifest,
    tabs: {}
  };

  // Broadcast start
  broadcastStatus(`🚀 Starting automation: ${manifest.query}`);

  for (const step of manifest.steps) {
    console.log(`▶ Step ${step.id} (${step.type})`);

    // Check dependencies
    if (step.dependencies && step.dependencies.length > 0) {
      const waitingFor = step.dependencies.filter(depId => !executionContext.results[depId]);
      if (waitingFor.length > 0) {
        console.warn(`⚠️ Step ${step.id} waiting for: ${waitingFor.join(', ')}`);
        // Real implementation would handle dependency DAG, here we assume linear/topological order in manifest
      }
    }

    try {
      const result = await executeStep(step);
      executionContext.results[step.id] = result;
      broadcastStatus(`✅ Step ${step.id} complete`);
    } catch (err) {
      console.error(`❌ Step ${step.id} failed:`, err);
      broadcastStatus(`❌ Step ${step.id} failed: ${err.message}`);
      if (manifest.errorHandling?.onStepFailure !== "CONTINUE_WITH_WARNING") {
        throw err;
      }
    }
  }

  broadcastStatus("🎉 Automation Plan Completed!");
  return executionContext.results;
}

async function executeStep(step) {
  switch (step.type) {
    case "SCRAPE_SEARCH_RESULTS": {
      // Assume we are on a Google Search page or need to open one
      // For sample, sidebar opens tabs? No, sidebar sends manifest.
      // We probably need to check if we have a search tab. 
      // If not, open one? 
      // The sidebar "Try Sample" opened tabs. But here we are "EXECUTE_MANIFEST".
      // Let's assume we need to open the search tab if not present.

      let tabId = executionContext.tabs['search'];
      if (!tabId) {
        broadcastStatus("🔍 Opening Google Search...");
        const query = step.config?.searchQuery || executionContext.manifest.query;
        const tab = await chrome.tabs.create({
          url: `https://www.google.com/search?q=${encodeURIComponent(query)}`,
          active: true
        });
        tabId = tab.id;
        executionContext.tabs['search'] = tabId;
        await waitForTabLoad(tabId);
      }

      broadcastStatus("🔍 Scraping search results...");
      return await runDOMStepOnTab(tabId, step);
    }

    case "AI_VALIDATE_RESULTS": {
      const depId = step.dependencies[0];
      const inputData = executionContext.results[depId];

      broadcastStatus("🤖 Validating results (Real AI)...");

      if (!Array.isArray(inputData)) {
        throw new Error(`Dependency ${depId} failed to return valid list results.`);
      }

      // CALL BACKEND TO FILTER RESULTS
      try {
        const response = await fetch('http://localhost:8000/agent/filter-results', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            goal: executionContext.manifest.query,
            results: inputData
          })
        });

        const data = await response.json();
        if (data.status === 'success' && data.selection?.selected_indices) {
          const indices = new Set(data.selection.selected_indices);
          return inputData.filter((_, i) => indices.has(i));
        } else {
          // Fallback if API fails
          console.warn("AI Filter failed, falling back to top 3");
          return inputData.slice(0, 3);
        }
      } catch (err) {
        console.error("AI Filter API error:", err);
        return inputData.slice(0, 3);
      }
    }

    case "OPEN_VALIDATED_URLS": {
      const items = executionContext.results[step.dependencies[0]];
      broadcastStatus(`🔗 Opening ${items.length} validated links...`);

      const openedTabs = [];
      for (const item of items) {
        const tab = await chrome.tabs.create({ url: item.url, active: false });
        openedTabs.push(tab.id);

        // 📸 Broadcast tab preview
        await broadcastTabPreview(tab, item);

        // Slight delay
        await new Promise(r => setTimeout(r, step.config.staggerDelay || 500));
      }
      executionContext.tabs['sub_tabs'] = openedTabs;
      return { opened: openedTabs.length };
    }

    case "OPEN_YOUTUBE_TAB": {
      broadcastStatus("🎥 Opening YouTube Search...");
      const query = step.config?.searchQuery || step.config?.youtubeQuery || executionContext.manifest.youtubeQuery || executionContext.manifest.query;
      const tab = await chrome.tabs.create({
        url: `https://www.youtube.com/results?search_query=${encodeURIComponent(query)}`,
        active: false
      });
      executionContext.tabs['youtube_search'] = tab.id;
      await waitForTabLoad(tab.id);
      return { tabId: tab.id };
    }

    case "NAVIGATE_TO": {
      broadcastStatus(`🌐 Navigating to ${step.config.url}...`);
      const tab = await chrome.tabs.create({
        url: step.config.url,
        active: true
      });
      executionContext.tabs['navigated_tab'] = tab.id;
      await waitForTabLoad(tab.id);

      // 📸 Broadcast tab preview
      await broadcastTabPreview(tab, { url: step.config.url, title: step.config.title || 'Page' });

      // 🧠 TRIGGER AI VALIDATION LOOP
      if (executionContext.manifest.enableAIValidation) {
        await runAIValidationLoop(tab.id, executionContext.manifest.query);
      }

      return { success: true, tabId: tab.id };
    }

    case "ANALYZE_PAGE": {
      // Run on opened sub-tabs
      const tabIds = executionContext.tabs['sub_tabs'] || [];
      broadcastStatus(`📄 Analyzing ${tabIds.length} pages in parallel...`);

      // PARALLEL EXECUTION
      const analyses = await Promise.all(tabIds.map(async (tabId) => {
        try {
          await waitForTabLoad(tabId);
          await new Promise(r => setTimeout(r, 1000)); // Short stabilization delay

          const result = await runDOMStepOnTab(tabId, step);

          // 🧠 TRIGGER AI VALIDATION LOOP FOR DEEP ANALYSIS
          if (executionContext.manifest.enableAIValidation) {
            // We handle error inside loop to not crash others
            try {
              broadcastStatus(`🕵️‍♀️ Deeply checking page ${tabId}...`);
              await runAIValidationLoop(tabId, executionContext.manifest.query);
            } catch (e) {
              console.warn(`AI loop failed for ${tabId}`, e);
            }
          }
          return result;
        } catch (e) {
          console.warn(`Failed to analyze tab ${tabId}`, e);
          return null;
        }
      }));

      return analyses.filter(r => r !== null);
    }

    case "SCRAPE_YOUTUBE_RESULTS": {
      const tabId = executionContext.tabs['youtube_search'];
      if (!tabId) throw new Error("No YouTube tab found");

      broadcastStatus("🎥 Scraping YouTube results...");
      return await runDOMStepOnTab(tabId, step);
    }

    default:
      console.warn(`Unknown step type: ${step.type}`);
      return {};
  }
}

async function runDOMStepOnTab(tabId, step) {
  // 1. Inject dom/domExecutor.js
  await chrome.scripting.executeScript({
    target: { tabId },
    files: ['dom/domExecutor.js']
  });

  // 2. Execute
  const result = await chrome.scripting.executeScript({
    target: { tabId },
    func: (s) => window.DOMExecutor.executeDOMStep(s),
    args: [step]
  });

  return result[0]?.result;
}

async function waitForTabLoad(tabId) {
  return new Promise(resolve => {
    const listener = (tid, changeInfo) => {
      if (tid === tabId && changeInfo.status === 'complete') {
        chrome.tabs.onUpdated.removeListener(listener);
        resolve();
      }
    };
    chrome.tabs.onUpdated.addListener(listener);
    chrome.tabs.get(tabId, tab => {
      if (chrome.runtime.lastError || !tab) {
        // Tab might be closed
        chrome.tabs.onUpdated.removeListener(listener);
        resolve();
        return;
      }
      if (tab.status === 'complete') {
        chrome.tabs.onUpdated.removeListener(listener);
        resolve();
      }
    });

    // ⏳ Timeout fallback (prevent hanging forever)
    setTimeout(() => {
      chrome.tabs.onUpdated.removeListener(listener);
      console.warn(`⏳ Tab load timed out for ${tabId}, proceeding mostly loaded.`);
      resolve();
    }, 10000); // 10 seconds max wait
  });
}

function broadcastStatus(msg) {
  // Send to sidebar to display
  chrome.runtime.sendMessage({
    type: "SYSTEM_STATUS",
    text: msg
  }).catch(() => { });
}
async function flushChatQueue() {
  if (!pendingChatMessage) return;
  try {
    await chrome.runtime.sendMessage({
      type: "CHAT_MESSAGE",
      text: pendingChatMessage.text,
      url: pendingChatMessage.url
    });
    pendingChatMessage = null;
  } catch (e) {
    console.log("⏳ Sidebar not ready yet.");
  }
}

async function flushExplanationQueue() {
  if (!pendingExplanation) return;
  try {
    await chrome.runtime.sendMessage({
      type: "EXPLAIN_SELECTION",
      text: pendingExplanation.text,
      url: pendingExplanation.url
    });
    pendingExplanation = null;
  } catch (e) {
    console.log("⏳ Sidebar not ready yet.");
  }
}

// ==================================================
// 🆕 TAB MANAGEMENT FUNCTIONS
// ==================================================

async function getAllTabs(sendResponse, message = {}) {
  try {
    const queryOptions = {};
    if (message.currentWindow) {
      queryOptions.currentWindow = true; // Use Chrome's native filtering
    } else if (message.windowId) {
      queryOptions.windowId = message.windowId;
    }

    const tabs = await chrome.tabs.query(queryOptions);

    // Filter out chrome:// and other internal URLs if needed
    // But user might want to see them to close them? Let's keep them if requested, 
    // or stick to existing filter for cleanliness. Sticking to cleanliness.
    const filteredTabs = tabs.filter(tab => {
      return tab.url &&
        !tab.url.startsWith('chrome-extension://');
      // !tab.url.startsWith('chrome://') && // Let user see system tabs in their window
      // !tab.url.startsWith('about:');
    });

    // Sort by index
    filteredTabs.sort((a, b) => a.index - b.index);

    sendResponse({
      success: true,
      tabs: filteredTabs.map(tab => ({
        id: tab.id,
        title: tab.title,
        url: tab.url,
        favIconUrl: tab.favIconUrl,
        active: tab.active,
        windowId: tab.windowId,
        index: tab.index
      }))
    });
  } catch (error) {
    console.error("Error getting tabs:", error);
    sendResponse({ success: false, error: error.message });
  }
}


async function switchToTab(tabId, sendResponse) {
  try {
    // Get tab info to know which window it's in
    const tab = await chrome.tabs.get(tabId);

    // Focus the window first
    await chrome.windows.update(tab.windowId, { focused: true });

    // Then activate the tab
    await chrome.tabs.update(tabId, { active: true });

    sendResponse({ success: true });
  } catch (error) {
    console.error("Error switching tab:", error);
    sendResponse({ success: false, error: error.message });
  }
}

async function closeTab(tabId, sendResponse) {
  try {
    await chrome.tabs.remove(tabId);
    sendResponse({ success: true });
  } catch (error) {
    console.error("Error closing tab:", error);
    sendResponse({ success: false, error: error.message });
  }
}

// ==================================================
// 🖱️ CONTEXT MENU
// ==================================================
chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: "explain-text",
    title: "Explain selected text",
    contexts: ["selection"]
  });
});

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  if (info.menuItemId !== "explain-text" || !tab) return;
  pendingExplanation = { text: info.selectionText, url: tab.url };
  await chrome.sidePanel.open({ tabId: tab.id });
  setTimeout(flushExplanationQueue, 50);
});

// ==================================================
// 🎬 RECORDING HELPERS
// ==================================================
async function startRecording(sendResponse) {
  await setupOffscreenDocument();
  chrome.runtime.sendMessage({ type: "START_RECORDING_OFFSCREEN" });
  sendResponse({ success: true });
}

async function stopRecording(sendResponse) {
  chrome.runtime.sendMessage({ type: "STOP_RECORDING_OFFSCREEN" });
  sendResponse({ success: true });
}

async function setupOffscreenDocument() {
  const contexts = await chrome.runtime.getContexts({ contextTypes: ["OFFSCREEN_DOCUMENT"] });
  if (contexts.length > 0) return;
  await chrome.offscreen.createDocument({
    justification: "Screen recording"
  });
}

// ==================================================
// 🧠 AI VALIDATION LOOP
// ==================================================
async function runAIValidationLoop(tabId, goal) {
  broadcastStatus("🤖 Starting AI Validation Loop...");
  const MAX_ITERATIONS = 5;

  for (let i = 0; i < MAX_ITERATIONS; i++) {
    console.log(`🔄 AI Loop Iteration ${i + 1}/${MAX_ITERATIONS}`);

    // 1. Extract Context (Reuse executeStep with ANALYZE_PAGE logic or custom script)
    // We'll use a direct lightweight script to get text context
    const contextResults = await chrome.scripting.executeScript({
      target: { tabId },
      func: () => {
        return {
          title: document.title,
          url: window.location.href,
          text: document.body.innerText.replace(/\s+/g, ' ').slice(0, 15000)
        };
      }
    });

    if (!contextResults || !contextResults[0]?.result) {
      console.warn("⚠️ Failed to extract context, stopping AI loop");
      break;
    }

    const { title, url, text } = contextResults[0].result;
    broadcastStatus(`🧠 Analyzing page: ${title.substring(0, 30)}...`);

    try {
      // 2. Ask AI Backend
      const response = await fetch('http://localhost:8000/agent/validate', {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          goal: goal,
          context: text,
          url: url,
          title: title
        })
      });

      const data = await response.json();

      if (data.status !== "success" || !data.micro_manifest) {
        console.error("❌ AI Validation API failed:", data);
        break;
      }

      const actions = data.micro_manifest.actions || [];
      if (actions.length === 0) {
        broadcastStatus("✅ AI found no further actions needed.");
        break;
      }

      broadcastStatus(`⚡ Executing ${actions.length} AI actions...`);

      // 3. Execute Actions
      const execResult = await runDOMStepOnTab(tabId, {
        type: "EXECUTE_MICRO_ACTIONS",
        actions: actions
      });

      // Check if finished
      if (execResult && execResult.finished) {
        broadcastStatus("🎉 AI Validation Completed Goal!");
        break;
      }

      // Small pause between iterations
      await new Promise(r => setTimeout(r, 2000));

    } catch (err) {
      console.error("❌ AI Loop Error:", err);
      broadcastStatus(`❌ AI Loop Error: ${err.message}`);
      break;
    }
  }
}
// ==================================================
// 📸 TAB PREVIEW BROADCASTING
// ==================================================

async function broadcastTabPreview(tab, metadata = {}) {
  try {
    // Wait a moment for the tab to load enough for screenshot
    await new Promise(r => setTimeout(r, 1000));

    // Capture thumbnail
    let thumbnail = null;
    try {
      thumbnail = await chrome.tabs.captureVisibleTab(tab.windowId, { format: "png" });
    } catch (e) {
      console.warn("Failed to capture tab thumbnail:", e);
    }

    // Send preview to sidebar
    chrome.runtime.sendMessage({
      type: "TAB_PREVIEW",
      tab: {
        id: tab.id,
        title: metadata.title || tab.title || "Loading...",
        url: tab.url,
        thumbnail: thumbnail,
        favIconUrl: tab.favIconUrl
      }
    }).catch(() => { });
  } catch (err) {
    console.warn("Failed to broadcast tab preview:", err);
  }
}

// ==================================================
// 📂 TAB GROUPING FUNCTIONS
// ==================================================

async function analyzeAndGroupTabs(sendResponse) {
  try {
    broadcastStatus("📂 Analyzing tabs for grouping...");

    // Get current window tabs
    const currentWindow = await chrome.windows.getCurrent();
    const tabs = await chrome.tabs.query({ windowId: currentWindow.id });

    // Filter out extension and chrome pages
    const validTabs = tabs.filter(tab =>
      tab.url &&
      !tab.url.startsWith('chrome://') &&
      !tab.url.startsWith('chrome-extension://') &&
      !tab.url.startsWith('about:')
    );

    if (validTabs.length < 2) {
      sendResponse({ success: false, error: "Not enough tabs to group (need at least 2)" });
      return;
    }

    broadcastStatus(`🔍 Extracting content from ${validTabs.length} tabs...`);

    // Extract content from each tab
    const tabData = [];
    for (const tab of validTabs) {
      try {
        const contentResults = await chrome.scripting.executeScript({
          target: { tabId: tab.id },
          func: () => {
            return {
              title: document.title,
              url: window.location.href,
              text: document.body.innerText.replace(/\s+/g, ' ').slice(0, 2000),
              description: document.querySelector('meta[name="description"]')?.content || ''
            };
          }
        });

        if (contentResults && contentResults[0]?.result) {
          tabData.push({
            id: tab.id,
            title: tab.title,
            url: tab.url,
            favIconUrl: tab.favIconUrl,
            ...contentResults[0].result
          });
        }
      } catch (e) {
        // Tab might not support script injection (e.g., PDF, restricted pages)
        console.warn(`Failed to extract content from tab ${tab.id}:`, e);
        tabData.push({
          id: tab.id,
          title: tab.title,
          url: tab.url,
          favIconUrl: tab.favIconUrl,
          text: ''
        });
      }
    }

    broadcastStatus("🤖 AI is analyzing tab content...");

    // Send to backend for AI analysis
    const response = await fetch('http://localhost:8000/tabs/analyze-content', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tabs: tabData })
    });

    const data = await response.json();

    if (!data.success || !data.groups) {
      throw new Error(data.error || "Failed to get grouping suggestions");
    }

    broadcastStatus(`✨ Applying ${data.groups.length} tab groups...`);

    // Apply Chrome tab groups
    await applyTabGroups(data.groups);

    // Send grouped tabs to sidebar
    chrome.runtime.sendMessage({
      type: "TABS_GROUPED",
      groups: data.groups
    }).catch(() => { });

    broadcastStatus("✅ Tabs grouped successfully!");
    sendResponse({ success: true, groups: data.groups });

  } catch (err) {
    console.error("Tab grouping failed:", err);
    broadcastStatus(`❌ Grouping failed: ${err.message}`);
    sendResponse({ success: false, error: err.message });
  }
}

async function applyTabGroups(groups) {
  const colors = ['blue', 'red', 'yellow', 'green', 'pink', 'purple', 'cyan', 'orange'];

  for (let i = 0; i < groups.length; i++) {
    const group = groups[i];
    const tabIds = group.tabs.map(t => t.id);

    if (tabIds.length > 0) {
      try {
        // Create a tab group
        const groupId = await chrome.tabs.group({ tabIds });

        // Update group properties
        await chrome.tabGroups.update(groupId, {
          title: group.topic,
          color: colors[i % colors.length],
          collapsed: false
        });
      } catch (e) {
        console.warn(`Failed to apply group "${group.topic}":`, e);
      }
    }
  }
}

async function toggleDarkSite(tabId) {
  try {
    await chrome.scripting.executeScript({
      target: { tabId },
      func: () => {
        const id = 'google-sidebar-theme-override';
        let style = document.getElementById(id);
        if (style) {
          style.remove();
        } else {
          style = document.createElement('style');
          style.id = id;
          style.textContent = `
            html { filter: invert(1) hue-rotate(180deg) !important; }
            img, video, iframe, canvas, [style*="background-image"] { 
              filter: invert(1) hue-rotate(180deg) !important; 
            }
            html { background-color: #000 !important; }
          `;
          document.documentElement.appendChild(style);
        }
      }
    });
  } catch (error) {
    console.error("Error toggling dark site:", error);
  }
}
