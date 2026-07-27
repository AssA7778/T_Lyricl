// ==UserScript==
// @name         tglyrics — ارسال موقعیت پخش به سرور
// @name:en      tglyrics — now-playing bridge
// @namespace    https://github.com/AssA7778/T_Lyricl
// @version      1.0.1
// @description  ثانیه‌ی دقیقِ آهنگی که داری گوش می‌دی رو به سرور tglyrics می‌فرسته تا لیریکش بره توی بیوی تلگرام
// @description:en  Sends the exact playback position of the current song to your tglyrics server
// @author       tglyrics
// @match        https://music.youtube.com/*
// @match        https://www.youtube.com/*
// @match        https://soundcloud.com/*
// @match        https://open.spotify.com/*
// @match        https://music.apple.com/*
// @match        https://listen.tidal.com/*
// @match        https://www.deezer.com/*
// @grant        GM_xmlhttpRequest
// @grant        GM_getValue
// @grant        GM_setValue
// @grant        GM_registerMenuCommand
// @connect      *
// @run-at       document-idle
// @noframes
// ==/UserScript==

(function () {
  'use strict';

  const HEARTBEAT_MS = 10000;
  const TICK_MS = 500;
  const SEEK_TOLERANCE_MS = 700;
  const RESYNC_MS = 15 * 60000;

  let SERVER = (GM_getValue('server', '') || '').replace(/\/+$/, '');
  let TOKEN = GM_getValue('token', '') || '';

  let clockOffset = 0;
  let clockRtt = null;
  let lastSyncAt = 0;

  let last = null;
  let lastSentAt = 0;
  let lastSeenPos = 0;
  let lastSeenAt = 0;

  const log = (...a) => console.log('%c[tglyrics]', 'color:#4ea1ff', ...a);
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  function gmFetch(method, url, body) {
    return new Promise((resolve, reject) => {
      GM_xmlhttpRequest({
        method,
        url,
        headers: Object.assign(
          { 'Content-Type': 'application/json' },
          TOKEN ? { Authorization: 'Bearer ' + TOKEN } : {}
        ),
        data: body ? JSON.stringify(body) : undefined,
        timeout: 8000,
        onload: (r) => (r.status >= 200 && r.status < 300
          ? resolve(r.responseText)
          : reject(new Error('HTTP ' + r.status + ' ' + r.responseText))),
        onerror: () => reject(new Error('network')),
        ontimeout: () => reject(new Error('timeout')),
      });
    });
  }

  async function syncClock() {
    if (!SERVER) return;
    let bestRtt = Infinity;
    let bestOffset = 0;
    for (let i = 0; i < 5; i++) {
      try {
        const t0 = Date.now();
        const txt = await gmFetch('GET', SERVER + '/time');
        const t3 = Date.now();
        const rtt = t3 - t0;
        const s = JSON.parse(txt).server_ms;
        if (rtt < bestRtt) {
          bestRtt = rtt;
          bestOffset = s - (t0 + t3) / 2;
        }
      } catch (e) {
      }
      await sleep(120);
    }
    if (bestRtt !== Infinity) {
      clockOffset = bestOffset;
      clockRtt = bestRtt;
      lastSyncAt = Date.now();
      log(`ساعت هماهنگ شد — اختلاف ${Math.round(clockOffset)}ms، RTT ${bestRtt}ms`);
    }
  }

  const serverNow = () => Date.now() + clockOffset;

  function pickMedia() {
    const els = Array.from(document.querySelectorAll('video, audio'));
    if (!els.length) return null;
    const usable = els.filter((e) => isFinite(e.duration) && e.duration > 1);
    const pool = usable.length ? usable : els;
    const playing = pool.filter((e) => !e.paused && !e.ended);
    const pick = (playing.length ? playing : pool);
    return pick.reduce((a, b) => ((b.duration || 0) > (a.duration || 0) ? b : a));
  }

  const txt = (sel) => {
    const n = document.querySelector(sel);
    return n ? (n.textContent || '').trim() : '';
  };

  function domMeta() {
    const h = location.host;
    if (h.includes('soundcloud.com')) {
      return {
        title: txt('.playbackSoundBadge__titleLink span:last-child')
            || txt('.playbackSoundBadge__titleLink'),
        artist: txt('.playbackSoundBadge__lightLink'),
      };
    }
    if (h.includes('open.spotify.com')) {
      return {
        title: txt('[data-testid="context-item-info-title"]')
            || txt('[data-testid="now-playing-widget"] a[data-testid="context-item-link"]'),
        artist: txt('[data-testid="context-item-info-artist"]')
            || txt('[data-testid="now-playing-widget"] a[href^="/artist"]'),
      };
    }
    if (h.includes('music.youtube.com')) {
      const bar = document.querySelector('ytmusic-player-bar');
      if (bar) {
        const sub = bar.querySelector('.subtitle');
        return {
          title: (bar.querySelector('.title') || {}).textContent?.trim() || '',
          artist: (sub ? sub.textContent : '').split('•')[0].trim(),
        };
      }
    }
    if (h.includes('music.apple.com')) {
      return {
        title: txt('.web-chrome-playback-lcd__song-name-scroll-inner-text-wrapper')
            || txt('[data-testid="lcd-title"]'),
        artist: txt('.web-chrome-playback-lcd__sub-copy-scroll-inner-text-wrapper')
            || txt('[data-testid="lcd-subtitle"]'),
      };
    }
    if (h.includes('youtube.com')) {
      return { title: txt('h1.ytd-watch-metadata'), artist: txt('#owner #channel-name a') };
    }
    return { title: '', artist: '' };
  }

  function meta() {
    const md = (navigator.mediaSession && navigator.mediaSession.metadata) || null;
    const dom = domMeta();
    let title = (md && md.title) || dom.title || '';
    let artist = (md && md.artist) || dom.artist || '';
    const album = (md && md.album) || '';

    if (!artist && title.includes(' - ')) {
      const i = title.indexOf(' - ');
      artist = title.slice(0, i).trim();
      title = title.slice(i + 3).trim();
    }
    artist = artist.replace(/\s*-\s*Topic\s*$/i, '').trim();
    return { title: title.trim(), artist, album };
  }

  function snapshot() {
    const el = pickMedia();
    if (!el) return null;
    const m = meta();
    if (!m.title) return null;
    const posMs = Math.max(0, Math.round((el.currentTime || 0) * 1000));
    return {
      event: 'state',
      title: m.title,
      artist: m.artist,
      album: m.album,
      duration_ms: isFinite(el.duration) ? Math.round(el.duration * 1000) : 0,
      position_ms: posMs,
      playing: !el.paused && !el.ended,
      rate: el.playbackRate || 1,
      captured_at_server_ms: serverNow(),
      agent: 'userscript/' + location.host,
    };
  }

  function shouldSend(s, now) {
    if (!last) return 'اولین';
    if (s.title !== last.title || s.artist !== last.artist) return 'آهنگ عوض شد';
    if (s.playing !== last.playing) return s.playing ? 'پلی' : 'پاز';
    if (Math.abs(s.rate - last.rate) > 0.01) return 'سرعت';

    if (s.playing && lastSeenAt) {
      const expected = lastSeenPos + (now - lastSeenAt) * (s.rate || 1);
      if (Math.abs(s.position_ms - expected) > SEEK_TOLERANCE_MS) return 'سیک';
    }
    if (now - lastSentAt >= HEARTBEAT_MS) return 'ضربان';
    return null;
  }

  async function send(s, why) {
    if (!SERVER) return;
    try {
      await gmFetch('POST', SERVER + '/ingest', Object.assign({ token: TOKEN }, s));
      last = s;
      lastSentAt = Date.now();
      if (why !== 'ضربان') log(`→ ${why}: ${s.artist} – ${s.title} @ ${(s.position_ms / 1000).toFixed(1)}s`);
    } catch (e) {
      log('ارسال نشد:', e.message);
    }
  }

  async function tick() {
    if (!SERVER) return;
    if (Date.now() - lastSyncAt > RESYNC_MS) await syncClock();

    const now = Date.now();
    const s = snapshot();
    if (!s) {
      if (last && last.playing) {
        await send(Object.assign({}, last, { playing: false, event: 'stop' }), 'توقف');
        last = null;
      }
      return;
    }
    const why = shouldSend(s, now);
    lastSeenPos = s.position_ms;
    lastSeenAt = now;
    if (why) await send(s, why);
  }

  function configure() {
    const url = prompt(
      'آدرس سرور tglyrics\nمثال:  http://1.2.3.4:8787  (با http:// یا https://)',
      SERVER || 'http://'
    );
    if (url === null) return;
    const tok = prompt('توکن (همان چیزی که توی config.toml گذاشتی)', TOKEN || '');
    if (tok === null) return;
    SERVER = url.trim().replace(/\/+$/, '');
    TOKEN = tok.trim();
    GM_setValue('server', SERVER);
    GM_setValue('token', TOKEN);
    lastSyncAt = 0;
    last = null;
    alert('ذخیره شد:\n' + SERVER + '\n\nصفحه را رفرش کن.');
  }

  async function testConn() {
    if (!SERVER) return alert('اول سرور را تنظیم کن.');
    try {
      const t = await gmFetch('GET', SERVER + '/health');
      alert('✅ سرور جواب داد:\n' + t + '\n\nاختلاف ساعت: ' +
        Math.round(clockOffset) + 'ms، RTT: ' + clockRtt + 'ms');
    } catch (e) {
      alert('❌ وصل نشد: ' + e.message +
        '\n\nچک کن: پورت روی فایروال باز است؟ آدرس درست است؟');
    }
  }

  GM_registerMenuCommand('⚙️ تنظیم سرور tglyrics', configure);
  GM_registerMenuCommand('🔌 تست اتصال', testConn);

  window.addEventListener('pagehide', () => {
    if (last && SERVER) {
      GM_xmlhttpRequest({
        method: 'POST',
        url: SERVER + '/ingest',
        headers: { 'Content-Type': 'application/json' },
        data: JSON.stringify({ token: TOKEN, event: 'stop' }),
      });
    }
  });

  (async () => {
    if (!SERVER) {
      log('هنوز سروری تنظیم نشده — از منوی افزونه «⚙️ تنظیم سرور tglyrics» را بزن.');
      return;
    }
    await syncClock();
    log('فعال شد →', SERVER);
    setInterval(() => { tick(); }, TICK_MS);
    tick();
  })();
})();
