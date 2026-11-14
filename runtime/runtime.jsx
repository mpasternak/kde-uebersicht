// Widget runtime compatible with the Übersicht API (https://github.com/felixhageloh/uebersicht).
//
// A widget is a module exporting (all optional):
//   command          string (shell command) | (dispatch) => void
//   refreshFrequency ms (default 1000) | false (run once)
//   render           (state, dispatch) => JSX
//   className        CSS string | style object — styles the widget container
//   initialState     initial state (default { output: "", error: null })
//   updateState      (event, prevState) => nextState
//   init             (dispatch) => void — called once after mounting
//
// The "uebersicht" module provides: run, css, styled, React (+ injectGlobal, keyframes).
// Commands are executed by the Python daemon via window.webkit.messageHandlers.

import React from "react";
import * as ReactDOM from "react-dom";
import { createRoot } from "react-dom/client";
import { css, cx, injectGlobal, keyframes } from "@emotion/css";
import styled from "@emotion/styled";
import { WidgetController, evaluateWidget } from "./core.js";

// ---------------------------------------------------------------- daemon bridge
function bridge(msg) {
  const h = window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.ubersicht;
  if (!h) return Promise.reject(new Error("no bridge to the daemon"));
  return h.postMessage(msg);
}

export function run(command) {
  return bridge({ op: "run", cmd: String(command) }).then((r) => {
    if (r.status === 0) return r.stdout;
    const err = new Error((r.stderr || r.stdout || `exit ${r.status}`).trim());
    err.stdout = r.stdout;
    err.status = r.status;
    throw err;
  });
}

const uebersicht = { run, css, cx, styled, React, injectGlobal, keyframes };
uebersicht.default = uebersicht;

const MODULES = {
  uebersicht,
  react: React,
  "react-dom": ReactDOM,
  "react-dom/client": { createRoot },
  "@emotion/css": { css, cx, injectGlobal, keyframes },
  "@emotion/styled": { default: styled, __esModule: true },
};
window.__ubsReact = React;
window.require = (name) => {
  if (name in MODULES) return MODULES[name];
  throw new Error(`Module "${name}" is not available (bundle it with the widget)`);
};

// ---------------------------------------------------------------- widgets
const BASE = css`
  position: absolute;
`;

function wrapperClass(className) {
  if (!className) return BASE;
  if (typeof className === "string") return cx(BASE, css(className));
  if (typeof className === "object") return cx(BASE, css(className));
  return BASE;
}

const errorBox = (title, text) => (
  <div
    style={{
      font: "12px monospace",
      background: "rgba(160,20,20,0.88)",
      color: "#fff",
      padding: "8px 10px",
      borderRadius: 6,
      maxWidth: 520,
      whiteSpace: "pre-wrap",
      margin: 4,
    }}
  >
    <b>{title}</b>
    {"\n"}
    {text}
  </div>
);

class Boundary extends React.Component {
  constructor(p) {
    super(p);
    this.state = { err: null };
  }
  static getDerivedStateFromError(err) {
    return { err };
  }
  componentDidUpdate(prev) {
    if (prev.tick !== this.props.tick && this.state.err) this.setState({ err: null });
  }
  render() {
    if (this.state.err) return errorBox(this.props.id, String(this.state.err && (this.state.err.stack || this.state.err)));
    return this.props.children;
  }
}

class Widget {
  constructor(id) {
    this.id = id;
    this.alive = true;
    this.tick = 0;
    this.ctl = null;
    this.el = document.createElement("div");
    this.el.id = id.replace(/[^A-Za-z0-9_-]/g, "-");
    this.el.dataset.widget = id;
    document.getElementById("ubs-root").appendChild(this.el);
    this.root = createRoot(this.el);
  }

  mount(code) {
    let mod;
    try {
      mod = evaluateWidget(code, this.id, window.require);
    } catch (e) {
      return this.fail("Load error", e);
    }
    this.el.className = "ubs-widget " + wrapperClass(mod.className);
    this.ctl = new WidgetController({
      id: this.id,
      mod,
      run,
      render: (state, dispatch) => this.render(mod, state, dispatch),
    });
    this.ctl.start();
  }

  fail(title, e) {
    this.el.className = "ubs-widget ubs-error";
    this.root.render(errorBox(`${this.id}: ${title}`, String((e && e.stack) || e)));
  }

  render(mod, state, dispatch) {
    if (!this.alive) return;
    this.tick++;
    let vdom;
    try {
      vdom = mod.render ? mod.render(state, dispatch) : <pre style={{ margin: 0 }}>{state && state.output}</pre>;
      // render may return an HTML string (older widget style)
      if (typeof vdom === "string") vdom = <div dangerouslySetInnerHTML={{ __html: vdom }} />;
    } catch (e) {
      vdom = errorBox(this.id, String(e.stack || e));
    }
    this.root.render(
      <Boundary id={this.id} tick={this.tick}>
        {vdom}
      </Boundary>
    );
  }

  refresh() {
    if (this.ctl) this.ctl.refresh();
  }

  destroy() {
    this.alive = false;
    if (this.ctl) this.ctl.destroy();
    try {
      this.root.unmount();
    } catch {
      /* already unmounted */
    }
    this.el.remove();
  }
}

// ---------------------------------------------------------------- management
const widgets = new Map();

async function load(id) {
  const old = widgets.get(id);
  if (old) old.destroy();
  const w = new Widget(id);
  widgets.set(id, w);
  const res = await bridge({ op: "compile", id });
  if (!w.alive) return;
  if (res.error) w.fail("Compile error", res.error);
  else w.mount(res.code);
  scheduleRegion();
}

async function sync(changed) {
  const { ids, hidden } = await bridge({ op: "list" });
  if (typeof hidden === "boolean") window.__ubs.setHidden(hidden);
  for (const [id, w] of widgets) {
    if (!ids.includes(id)) {
      w.destroy();
      widgets.delete(id);
    }
  }
  for (const id of ids) {
    if (!widgets.has(id) || !changed || changed.includes(id)) await load(id);
  }
  scheduleRegion();
}

// ---------------------------------------------------------------- input region
// The window covers the whole screen, but clicks should reach widgets only where
// something is drawn — everything else passes through to the desktop (desktop icons etc.).
let regionTimer = null;
let lastRegion = "";
function collectRects() {
  const rects = [];
  const walk = (el) => {
    const cs = getComputedStyle(el);
    if (cs.display === "none") return;
    const r = el.getBoundingClientRect();
    if (r.width >= 1 && r.height >= 1 && cs.visibility !== "hidden" && cs.pointerEvents !== "none") {
      rects.push([Math.floor(r.left), Math.floor(r.top), Math.ceil(r.width) + 1, Math.ceil(r.height) + 1]);
    }
    for (const c of el.children) walk(c);
  };
  for (const w of document.querySelectorAll(".ubs-widget")) walk(w);
  return rects.slice(0, 4000);
}
function sendRegion() {
  regionTimer = null;
  const rects = collectRects();
  const key = JSON.stringify([rects, window.innerWidth, window.innerHeight]);
  if (key === lastRegion) return;
  lastRegion = key;
  // viewport lets the daemon convert CSS px to window px when the page is zoomed
  bridge({ op: "region", rects, viewport: [window.innerWidth, window.innerHeight] }).catch(() => {});
}
function scheduleRegion() {
  if (!regionTimer) regionTimer = setTimeout(sendRegion, 120);
}

new MutationObserver(scheduleRegion).observe(document.documentElement, {
  subtree: true,
  childList: true,
  attributes: true,
  characterData: true,
});
window.addEventListener("resize", scheduleRegion);
window.addEventListener("mouseup", () => setTimeout(sendRegion, 0));
window.addEventListener("load", scheduleRegion);
setInterval(scheduleRegion, 2000); // CSS animations / late-loading images

// ---------------------------------------------------------------- API for the daemon
window.__ubs = {
  sync,
  reload: (ids) => sync(ids || null),
  resendRegion: () => {
    lastRegion = "";
    sendRegion();
  },
  refreshAll: () => {
    for (const w of widgets.values()) w.refresh();
  },
  setHidden: (hidden) => {
    document.body.style.visibility = hidden ? "hidden" : "";
    lastRegion = "";
    sendRegion();
  },
};

sync(null).catch((e) => console.error("ubs sync", e));
