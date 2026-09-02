/* Malaria AI Assistant - modern vanilla JS chat UI */

(() => {
  const els = {
    messages: document.getElementById('messages'),
    typing: document.getElementById('typing'),
    input: document.getElementById('messageInput'),
    send: document.getElementById('sendBtn'),
    btnNewChat: document.getElementById('btnNewChat'),
    btnClearHistory: document.getElementById('btnClearHistory'),
    themeToggle: document.getElementById('themeToggle'),
    btnCopy: document.getElementById('btnCopy'),
    btnRetry: document.getElementById('btnRetry'),
    btnMic: document.getElementById('btnMic'),
    voiceMsg: document.getElementById('voiceMsg'),
    autoReadToggle: document.getElementById('autoReadToggle'),
  };

  const STORAGE_KEY = 'malaria_chat_history_v1';
  const STORAGE_PREFS_KEY = 'malaria_chat_prefs_v1';

  const state = {
    messages: [],
    waiting: false,
    lastUserMessage: null,
    lastAIMessageText: '',
    botIdCounter: 1,
    // Voice output: in-memory only (localStorage is not reliable here).
    autoRead: false,
    speakingId: null,
  };

  // ---- Voice recording state ----
  let mediaRecorder = null;
  let audioChunks = [];
  let isRecording = false;
  let voiceMsgTimer = null;

  // ---- Inline voice status / error messaging (no alert()) ----

  function showVoiceMsg(text, isError = false, autoHideMs = 0) {
    if (!els.voiceMsg) return;
    clearTimeout(voiceMsgTimer);
    els.voiceMsg.textContent = text;
    els.voiceMsg.classList.toggle('error', !!isError);
    els.voiceMsg.classList.remove('hidden');
    if (autoHideMs > 0) {
      voiceMsgTimer = setTimeout(hideVoiceMsg, autoHideMs);
    }
  }

  function hideVoiceMsg() {
    if (!els.voiceMsg) return;
    clearTimeout(voiceMsgTimer);
    els.voiceMsg.classList.add('hidden');
    els.voiceMsg.classList.remove('error');
    els.voiceMsg.textContent = '';
  }

  // ---- Voice output (browser-native TTS, no backend) ----

  function stopSpeaking() {
    try {
      if (window.speechSynthesis) window.speechSynthesis.cancel();
    } catch (_) { /* ignore */ }
    els.messages
      .querySelectorAll('.speakBtn.speaking')
      .forEach((b) => b.classList.remove('speaking'));
    state.speakingId = null;
  }

  function speak(text, id, btnEl) {
    if (!('speechSynthesis' in window) || typeof SpeechSynthesisUtterance === 'undefined') {
      showVoiceMsg('Text-to-speech is not supported in this browser.', true, 4000);
      return;
    }
    const clean = String(text || '').trim();
    if (!clean) return;

    // Only ever one utterance at a time.
    stopSpeaking();

    const utter = new SpeechSynthesisUtterance(clean);
    utter.rate = 1;
    utter.pitch = 1;
    const clearBtn = () => {
      if (btnEl) btnEl.classList.remove('speaking');
      if (state.speakingId === (id || 'auto')) state.speakingId = null;
    };
    utter.onend = clearBtn;
    utter.onerror = clearBtn;

    state.speakingId = id || 'auto';
    if (btnEl) btnEl.classList.add('speaking');
    window.speechSynthesis.speak(utter);
  }

  function toggleSpeak(text, id, btnEl) {
    if (state.speakingId && state.speakingId === (id || 'auto')) {
      stopSpeaking();
      return;
    }
    speak(text, id, btnEl);
  }

  // ---- In-flight request tracking (lets us cancel cleanly on New Chat / Clear History) ----
  let activeController = null;

  function nowLabel(d = new Date()) {
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }

  function escapeHtml(str) {
    return String(str).replace(/[&<>"']/g, (c) => ({
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#39;'
    }[c]));
  }

  function getTheme() {
    const stored = safeJsonParse(localStorage.getItem(STORAGE_PREFS_KEY));
    return stored && stored.theme ? stored.theme : null;
  }

  function setTheme(theme) {
    localStorage.setItem(STORAGE_PREFS_KEY, JSON.stringify({ theme }));
    document.documentElement.setAttribute('data-theme', theme);
    if (els.themeToggle) els.themeToggle.checked = theme === 'dark';
  }

  function applyInitialTheme() {
    const stored = getTheme();
    if (stored) return setTheme(stored);

    const prefersDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
    setTheme(prefersDark ? 'dark' : 'light');
  }

  function safeJsonParse(s) {
    try { return JSON.parse(s); } catch { return null; }
  }

  function persistMessages() {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state.messages));
  }

  function setWaiting(isWaiting) {
    state.waiting = isWaiting;
    els.send && (els.send.disabled = isWaiting);
    els.input && (els.input.disabled = isWaiting);
    els.btnMic && (els.btnMic.disabled = isWaiting);
    els.typing && els.typing.classList.toggle('hidden', !isWaiting);
  }

  function autoscroll() {
    els.messages.scrollTop = els.messages.scrollHeight;
  }

  function createMessageRow(role, text, metaTime, messageId) {
    const row = document.createElement('div');
    row.className = `row ${role}`;

    const bubble = document.createElement('div');
    bubble.className = 'bubble';
    bubble.setAttribute('data-role', role);

    const timeHtml = metaTime ? `<div class="bmeta">${escapeHtml(metaTime)}</div>` : '';

    bubble.innerHTML = `${escapeHtml(text)}${timeHtml}`;

    // Speaker button on bot replies that actually have text (skips the empty
    // streaming placeholder). Appended as a DOM node so escapeHtml stays intact.
    if (role === 'bot' && String(text || '').trim()) {
      const actions = document.createElement('div');
      actions.className = 'msgActions';

      const spk = document.createElement('button');
      spk.type = 'button';
      spk.className = 'smallBtn speakBtn';
      spk.title = 'Read this reply aloud';
      spk.setAttribute('aria-label', 'Read this reply aloud');
      spk.textContent = '🔊';
      if (messageId) spk.dataset.speakId = messageId;

      actions.appendChild(spk);
      bubble.appendChild(actions);
    }

    row.appendChild(bubble);

    return { row, bubble };
  }

  function renderFromState() {
    els.messages.innerHTML = '';

    for (const msg of state.messages) {
      const { row, bubble } = createMessageRow(msg.role, msg.text, msg.time, msg.id);
      els.messages.appendChild(row);
      if (msg.role === 'bot') {
        bubble.dataset.messageId = msg.id;
      }
    }

    autoscroll();
  }

  function addMessage(role, text, { save = true } = {}) {
    const msg = {
      id: `${Date.now()}_${state.botIdCounter++}`,
      role,
      text,
      time: nowLabel(),
    };

    state.messages.push(msg);
    if (save) persistMessages();

    const { row, bubble } = createMessageRow(role, text, msg.time, msg.id);
    if (role === 'bot') bubble.dataset.messageId = msg.id;

    els.messages.appendChild(row);
    autoscroll();
    return { msg, row, bubble };
  }

  function showTyping(show) {
    if (!els.typing) return;
    els.typing.classList.toggle('hidden', !show);
  }

  function updateBubbleText(messageId, partialText, metaTime) {
    const bubble = els.messages.querySelector(`.bubble[data-message-id="${CSS.escape(messageId)}"]`);
    if (!bubble) return;
    bubble.innerHTML = `${escapeHtml(partialText)}<div class="bmeta">${escapeHtml(metaTime)}</div>`;
    autoscroll();
  }

  /**
   * Streams the chat response. Accepts an externally created AbortController
   * so callers (e.g. "New Chat") can cancel an in-flight request cleanly.
   */
  async function callChatStream(message, onChunk, controller) {
    const inactivityTimeoutMs = 120000; // abort if no new data for 2 minutes
    let lastActivity = Date.now();
    let gotAnyData = false;

    const watchdog = setInterval(() => {
      if (Date.now() - lastActivity > inactivityTimeoutMs) {
        // Give the abort a reason so it's distinguishable in logs/catch blocks
        controller.abort('inactivity-timeout');
      }
    }, 1000);

    const startedAt = Date.now();

    try {
      const res = await fetch('/chat/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message }),
        signal: controller.signal,
      });

      if (!res.ok || !res.body) {
        const text = await res.text().catch(() => '');
        throw new Error(text || `Server error: ${res.status}`);
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let full = '';
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        gotAnyData = true;
        lastActivity = Date.now();
        buffer += decoder.decode(value, { stream: true });

        const parts = buffer.split('\n\n');
        buffer = parts.pop(); // keep incomplete chunk in buffer

        for (const part of parts) {
          for (const line of part.split('\n')) {
            if (line.startsWith('data: ')) {
              const data = line.slice(6);
              if (data === '[DONE]') continue;
              full += data;
              onChunk(full);
            }
          }
        }
      }

      return full;

    } catch (error) {
      const elapsedMs = Date.now() - startedAt;
      // Surface exactly why the abort happened - watchdog, external cancel
      // (e.g. New Chat clicked), or a browser/navigation-level cancellation.
      console.error('Chat Stream Error:', error, {
        reason: controller.signal.reason,
        elapsedMs,
        gotAnyData,
      });

      if (error && error.name === 'AbortError') {
        if (controller.signal.reason === 'user-cancelled') {
          throw new Error('Cancelled.');
        }
        if (controller.signal.reason === 'inactivity-timeout') {
          throw new Error('AI response stalled. Please try again.');
        }
        // Aborted for an unknown reason (e.g. page navigation/reload,
        // dev-server restart) - not something the watchdog caused.
        throw new Error('Request was interrupted. Please try again.');
      }
      throw error;

    } finally {
      clearInterval(watchdog);
    }
  }

  async function sendMessage({ retry = false } = {}) {
    if (state.waiting) return;

    const message = (els.input.value || '').trim();
    if (!message && !retry) return;

    const userText = retry ? state.lastUserMessage : message;
    if (!userText) return;

    if (!retry) {
      state.lastUserMessage = userText;
    }

    els.input.value = '';

    addMessage('user', userText);

    setWaiting(true);

    const placeholder = addMessage('bot', '', { save: false });
    placeholder.bubble.dataset.messageId = placeholder.msg.id;

    // Track this request so it can be cancelled externally (New Chat, Clear History)
    const controller = new AbortController();
    activeController = controller;

    try {
      const fullReply = await callChatStream(userText, (partialText) => {
        showTyping(false);
        updateBubbleText(placeholder.msg.id, partialText, placeholder.msg.time);
      }, controller);

      const i = state.messages.findIndex(m => m.id === placeholder.msg.id);
      if (i >= 0) {
        state.messages[i].text = fullReply;
      }
      persistMessages();
      state.lastAIMessageText = fullReply;

      // Re-render so the finished bot bubble gets its speaker button
      // (streaming updates overwrite the bubble's inner HTML).
      renderFromState();

      // Voice output fires only on the complete reply, never mid-stream.
      if (state.autoRead && fullReply && fullReply.trim()) {
        const spk = els.messages.querySelector(
          `.bubble[data-message-id="${CSS.escape(placeholder.msg.id)}"] .speakBtn`
        );
        speak(fullReply, placeholder.msg.id, spk || null);
      }

    } catch (err) {
      const errMsg = err && err.message ? err.message : 'Unable to reach chat service. Please try again.';
      const i = state.messages.findIndex(m => m.id === placeholder.msg.id);
      if (i >= 0) state.messages[i].text = errMsg;

      updateBubbleText(placeholder.msg.id, errMsg, placeholder.msg.time);
      persistMessages();
      state.lastAIMessageText = errMsg;
    } finally {
      if (activeController === controller) activeController = null;
      setWaiting(false);
      els.input && els.input.focus();
    }
  }

  function cancelActiveRequest() {
    if (activeController) {
      activeController.abort('user-cancelled');
      activeController = null;
    }
  }

  // ---- Voice recording functions ----

  async function startRecording() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || typeof MediaRecorder === 'undefined') {
      showVoiceMsg('Voice recording is not supported in this browser.', true, 5000);
      return;
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });

      // Record webm (what the /voice/transcribe temp file expects).
      let options;
      if (MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported('audio/webm;codecs=opus')) {
        options = { mimeType: 'audio/webm;codecs=opus' };
      } else if (MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported('audio/webm')) {
        options = { mimeType: 'audio/webm' };
      }
      mediaRecorder = options ? new MediaRecorder(stream, options) : new MediaRecorder(stream);
      audioChunks = [];

      mediaRecorder.ondataavailable = (e) => {
        if (e.data.size > 0) audioChunks.push(e.data);
      };

      mediaRecorder.start();
      isRecording = true;

      if (els.btnMic) {
        els.btnMic.textContent = '⏹️';
        els.btnMic.classList.add('recording');
        els.btnMic.title = 'Stop recording';
      }
      showVoiceMsg('Listening… tap the mic again to stop.', false);
    } catch (err) {
      console.error('Mic access failed:', err);
      const name = err && err.name;
      if (name === 'NotAllowedError' || name === 'SecurityError') {
        showVoiceMsg('Microphone permission denied. Allow mic access in your browser settings, then try again.', true, 7000);
      } else if (name === 'NotFoundError' || name === 'DevicesNotFoundError') {
        showVoiceMsg('No microphone found. Connect one and try again.', true, 7000);
      } else {
        showVoiceMsg('Could not start recording: ' + ((err && err.message) || 'unknown error'), true, 7000);
      }
    }
  }

  function stopRecordingAndTranscribe() {
    return new Promise((resolve, reject) => {
      if (!mediaRecorder) {
        reject(new Error('Not currently recording'));
        return;
      }

      mediaRecorder.onstop = async () => {
        isRecording = false;
        if (els.btnMic) {
          els.btnMic.textContent = '🎤';
          els.btnMic.classList.remove('recording');
          els.btnMic.title = 'Record a voice message';
        }

        const blob = new Blob(audioChunks, { type: 'audio/webm' });

        if (blob.size === 0) {
          reject(new Error('No audio captured. Please try again.'));
          return;
        }

        const formData = new FormData();
        formData.append('audio', blob, 'recording.webm');

        try {
          const res = await fetch('/voice/transcribe', {
            method: 'POST',
            body: formData,
          });

          const data = await res.json().catch(() => ({}));

          if (!res.ok || data.error) {
            reject(new Error(data.error || `Transcription failed (${res.status})`));
            return;
          }

          resolve(data.text || '');
        } catch (err) {
          reject(err);
        }
      };

      mediaRecorder.stop();
      mediaRecorder.stream.getTracks().forEach(track => track.stop());
    });
  }

  async function handleMicClick() {
    if (state.waiting) return;

    if (!isRecording) {
      hideVoiceMsg();
      await startRecording();
      return;
    }

    // Currently recording -> stop and transcribe.
    if (els.btnMic) els.btnMic.disabled = true;
    showVoiceMsg('Transcribing your recording…', false);

    try {
      const transcribedText = await stopRecordingAndTranscribe();

      if (transcribedText && transcribedText.trim()) {
        // Populate the input for review/edit - do NOT auto-send.
        const existing = (els.input.value || '').trim();
        els.input.value = existing
          ? `${existing} ${transcribedText.trim()}`
          : transcribedText.trim();
        els.input.focus();
        showVoiceMsg('Transcribed - review the text, then press Send.', false, 4000);
      } else {
        showVoiceMsg("Couldn't make out any speech. Please try again.", true, 5000);
      }
    } catch (err) {
      console.error('Transcription error:', err);
      showVoiceMsg('Voice transcription failed: ' + ((err && err.message) || 'unknown error'), true, 6000);
    } finally {
      if (els.btnMic) els.btnMic.disabled = false;
    }
  }

  function initEvents() {
    if (els.send) {
      els.send.addEventListener('click', () => sendMessage());
    }

    if (els.input) {
      els.input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
          e.preventDefault();
          sendMessage();
        }
      });
    }

    if (els.btnMic) {
      els.btnMic.addEventListener('click', handleMicClick);
    }

    if (els.autoReadToggle) {
      els.autoReadToggle.addEventListener('change', (e) => {
        state.autoRead = !!e.target.checked;
        if (!state.autoRead) stopSpeaking();
      });
    }

    // Per-reply speaker buttons (event delegation - survives re-renders).
    if (els.messages) {
      els.messages.addEventListener('click', (e) => {
        const btn = e.target.closest('.speakBtn');
        if (!btn) return;
        const id = btn.dataset.speakId || null;
        const msg = id ? state.messages.find((m) => m.id === id) : null;
        const text = msg ? msg.text : (btn.closest('.bubble')?.textContent || '');
        toggleSpeak(text, id, btn);
      });
    }

    if (els.btnNewChat) {
      els.btnNewChat.addEventListener('click', () => {
        cancelActiveRequest();
        stopSpeaking();
        setWaiting(false);

        state.messages = [];
        state.lastUserMessage = null;
        state.lastAIMessageText = '';
        persistMessages();
        renderFromState();

        state.messages.push({
          id: `${Date.now()}_g1`,
          role: 'bot',
          text: 'Hi! Ask me anything about malaria symptoms, prevention, diagnosis, or what to do next.',
          time: nowLabel()
        });
        persistMessages();
        renderFromState();
      });
    }

    if (els.btnClearHistory) {
      els.btnClearHistory.addEventListener('click', () => {
        cancelActiveRequest();
        stopSpeaking();
        setWaiting(false);

        state.messages = [];
        state.lastUserMessage = null;
        state.lastAIMessageText = '';
        localStorage.removeItem(STORAGE_KEY);
        persistMessages();
        renderFromState();
        els.input && els.input.focus();

        // Also clear the server-side ChatMessage history for this session -
        // without this, "Clear history" only hid messages locally while the
        // conversation (which can include symptom descriptions) stayed in
        // the database indefinitely with no other way to delete it. Fire
        // and forget: the local clear above already happened regardless of
        // network state.
        fetch('/clear', { method: 'POST' }).catch(() => {});
      });
    }

    if (els.themeToggle) {
      els.themeToggle.addEventListener('change', (e) => {
        setTheme(e.target.checked ? 'dark' : 'light');
      });
    }

    if (els.btnRetry) {
      els.btnRetry.addEventListener('click', () => {
        if (!state.lastUserMessage) return;
        sendMessage({ retry: true });
      });
    }

    if (els.btnCopy) {
      els.btnCopy.addEventListener('click', async () => {
        if (!state.lastAIMessageText) return;
        try {
          await navigator.clipboard.writeText(state.lastAIMessageText);
          els.btnCopy.textContent = '✓';
          setTimeout(() => els.btnCopy.textContent = '⧉', 900);
        } catch {
          alert('Copy failed in this browser.');
        }
      });
    }
  }

  function loadSession() {
    const stored = safeJsonParse(localStorage.getItem(STORAGE_KEY));
    if (stored && Array.isArray(stored) && stored.length) {
      state.messages = stored;
      const lastUser = [...stored].reverse().find(m => m.role === 'user');
      state.lastUserMessage = lastUser ? lastUser.text : null;
      const lastBot = [...stored].reverse().find(m => m.role === 'bot' && m.text);
      state.lastAIMessageText = lastBot ? lastBot.text : '';
      renderFromState();
      return;
    }

    state.messages = [
      { id: `${Date.now()}_b0`, role: 'bot', text: 'Hi! Ask me anything about malaria symptoms, prevention, diagnosis, or what to do next.', time: nowLabel() }
    ];
    persistMessages();
    renderFromState();
  }

  function boot() {
    applyInitialTheme();
    initEvents();
    loadSession();
  }

  boot();
})();