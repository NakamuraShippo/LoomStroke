"""Native ComfyUI browser scripts, packaged with the Python module."""

# ruff: noqa: E501 - embedded JavaScript retains its native formatting

HIERARCHY = r"""/* Resolve nodes by their instance path without flattening or copying subgraphs. */
(() => {
  window.createProbeHierarchy = (app, canvas) => {
    const children = node => node.isSubgraphNode?.() ? node.subgraph : null;
    const walk = (graph = app.graph, prefix = [], ancestors = new Set()) => {
      if (ancestors.has(graph)) throw new Error('Recursive subgraph definition');
      const next = new Set([...ancestors, graph]);
      return graph._nodes.flatMap(node => {
        const path = [...prefix, String(node.id)];
        const subgraph = children(node);
        return [{node, graph, path}, ...(subgraph ? walk(subgraph, path, next) : [])];
      });
    };
    const resolve = value => {
      const path = (Array.isArray(value) ? value : [value]).map(String);
      const entry = walk().find(e => JSON.stringify(e.path) === JSON.stringify(path));
      if (!entry) throw new Error(`Node not found: ${path.join(' / ')}`);
      return entry;
    };
    const root = () => {
      canvas.subgraph = undefined;
      canvas.setGraph(app.graph);
    };
    const navigate = entry => {
      if (canvas.graph === entry.graph) return;
      root();
      let graph = app.graph;
      for (const id of entry.path.slice(0, -1)) {
        const parent = graph.getNodeById(id);
        const subgraph = children(parent);
        if (!subgraph) throw new Error(`Missing subgraph: ${id}`);
        canvas.openSubgraph(subgraph, parent);
        if (canvas.graph !== subgraph) throw new Error(`Subgraph navigation cancelled: ${id}`);
        graph = subgraph;
      }
    };
    const fingerprint = () => JSON.stringify(walk().map(({node, graph, path}) => ({
      path, type: node.type, mode: node.mode, links: graph.serialize().links,
      inputs: (node.inputs ?? []).map(i => [i.name, i.type, i.link]),
      outputs: (node.outputs ?? []).map(o => [o.name, o.type, o.links]),
    })).sort((a,b) => JSON.stringify(a.path).localeCompare(JSON.stringify(b.path))));
    return {walk, resolve, navigate, root, fingerprint};
  };
})()"""

FRONTEND = r"""/* Compatibility probe for the real ComfyUI frontend, never a widget reimplementation. */
(async () => {
  const app = window.comfyAPI.app.app;
  const graphCanvas = app.canvas;
  if (!graphCanvas?.canvas || !app.graph) throw new Error('ComfyUI canvas not ready');
  const canvas = graphCanvas.canvas;
  const LiteGraph = window.LiteGraph ?? window.comfyAPI?.litegraph?.LiteGraph;
  if (!LiteGraph?.createNode) throw new Error('LiteGraph compatibility API unavailable');
  let selected = null;
  let selectedPath = [];
  let mode = 'parameters';
  let topology = '';
  let loading = false;
  let focusRevision = 0;
  // A dock/tab may become hidden while a native operation is in flight.
  // Chromium suspends animation frames then; retain a bounded layout wait.
  const nextFrame = () => new Promise(resolve => {
    const timer = setTimeout(resolve, 100);
    requestAnimationFrame(() => { clearTimeout(timer); resolve(); });
  });
  const hierarchy = window.createProbeHierarchy(app, graphCanvas);
  const fingerprint = hierarchy.fingerprint;
  const originalFlags = Object.fromEntries(['allow_dragnodes', 'allow_reconnect_links',
    'allow_searchbox', 'show_info'].map(key => [key, graphCanvas[key]]));
  const originalVisible = graphCanvas.computeVisibleNodes;
  const originalConnections = graphCanvas.drawConnections;
  const originalGroups = graphCanvas.drawGroups;
  graphCanvas.computeVisibleNodes = function (...args) {
    const visible = originalVisible.apply(this, args);
    if (mode === 'workflow') return visible;
    const retained = visible.filter(n => String(n.id) === String(selected));
    visible.splice(0, visible.length, ...retained);
    return visible;
  };
  graphCanvas.drawConnections = function (...args) {
    if (mode === 'workflow') return originalConnections.apply(this, args);
  };
  graphCanvas.drawGroups = function (...args) {
    if (mode === 'workflow') return originalGroups?.apply(this, args);
  };
  graphCanvas.allow_dragnodes = false;
  graphCanvas.allow_reconnect_links = false;
  graphCanvas.allow_searchbox = false;
  graphCanvas.show_info = false;
  // DOM widgets are siblings of the canvas in current ComfyUI; preserve this subtree.
  const style = document.createElement('style');
  style.textContent = `
    body * { visibility: hidden !important; }
    #graph-canvas, .dom-widget, .dom-widget *, [data-probe-widget], [data-probe-widget] *,
    .graphdialog, .graphdialog *, [role=listbox], [role=listbox] *,
    .litegraph.litecontextmenu, .litegraph.litecontextmenu * {
      visibility: visible !important; }
    #graph-canvas { position: fixed !important; inset: 0 !important;
      width:100vw !important; height:100vh !important; }
  `;
  document.head.appendChild(style);
  // Block graph editing gestures, while preserving text/DOM/iframe widget interactions.
  const block = e => { e.preventDefault(); e.stopImmediatePropagation(); };
  const blockInParameters = e => { if (mode === 'parameters') block(e); };
  canvas.addEventListener('dblclick', blockInParameters, true);
  canvas.addEventListener('contextmenu', blockInParameters, true);
  document.addEventListener('keydown', e => {
    if (mode === 'workflow') return;
    const input = e.target.closest?.('input,textarea,[contenteditable="true"]');
    if (!input && (['Delete', 'Backspace'].includes(e.key) || e.ctrlKey || e.metaKey)) block(e);
  }, true);
  for (const event of ['pointerdown', 'mousedown']) canvas.addEventListener(event, e => {
    if (mode === 'workflow') return;
    const node = graphCanvas.graph.getNodeById(selected);
    const rect = canvas.getBoundingClientRect();
    const x = (e.clientX - rect.left) / graphCanvas.ds.scale - graphCanvas.ds.offset[0];
    const y = (e.clientY - rect.top) / graphCanvas.ds.scale - graphCanvas.ds.offset[1];
    // Socket edges and title bars are not value editors.
    if (!node || x < node.pos[0] + 18 || x > node.pos[0] + node.size[0] - 18 ||
        y < node.pos[1] || y > node.pos[1] + node.size[1]) block(e);
  }, true);
  const fit = node => {
    node.updateArea?.(graphCanvas.ctx);
    graphCanvas.resize(window.innerWidth, window.innerHeight);
    const scale = Math.min(1.3, (canvas.clientWidth - 60) / node.size[0],
      (canvas.clientHeight - 80) / (node.size[1] + 40));
    graphCanvas.ds.scale = Math.max(0.1, scale);
    graphCanvas.ds.offset[0] = (canvas.clientWidth / scale - node.size[0]) / 2 - node.pos[0];
    graphCanvas.ds.offset[1] = (canvas.clientHeight / scale - node.size[1]) / 2 - node.pos[1] + 15;
    graphCanvas.setDirty(true, true);
  };
  const fitGraph = () => {
    graphCanvas.resize();
    const nodes = graphCanvas.graph._nodes;
    if (!nodes.length) return;
    const left = Math.min(...nodes.map(n => n.pos[0])) - 50;
    const top = Math.min(...nodes.map(n => n.pos[1])) - 80;
    const right = Math.max(...nodes.map(n => n.pos[0] + n.size[0])) + 50;
    const bottom = Math.max(...nodes.map(n => n.pos[1] + n.size[1])) + 50;
    const scale = Math.min(1, canvas.clientWidth / (right-left), canvas.clientHeight / (bottom-top));
    graphCanvas.ds.scale = Math.max(.05, scale);
    graphCanvas.ds.offset[0] = (canvas.clientWidth / scale - (right-left))/2-left;
    graphCanvas.ds.offset[1] = (canvas.clientHeight / scale - (bottom-top))/2-top;
    graphCanvas.setDirty(true, true);
  };
  const focus = async id => {
    const revision = ++focusRevision;
    const entry = hierarchy.resolve(id);
    const {node, path} = entry;
    hierarchy.navigate(entry);
    selected = node.id;
    selectedPath = path;
    document.querySelectorAll('[data-probe-widget]').forEach(e => e.removeAttribute('data-probe-widget'));
    for (const widget of node.widgets ?? []) widget.element?.setAttribute('data-probe-widget', '');
    // DOM widgets settle their minimum size during actual draws, not at construction.
    for (let i = 0; i < 6; i++) {
      if (revision !== focusRevision) return describe();
      if (mode === 'parameters') fit(node);
      else fitGraph();
      await nextFrame();
    }
    return describe();
  };
  const describe = () => ({
    selected, selectedPath, mode,
    nodes: hierarchy.walk().map(({node:n, path}) => ({id: n.id, path,
      subgraph: Boolean(n.isSubgraphNode?.()), type: n.type, title: n.title,
      widgets: (n.widgets ?? []).map(w => ({name: w.name, type: w.type,
        dom: Boolean(w.element), value: w.value}))})),
    canvas: {width: canvas.width, height: canvas.height},
  });
  const checkTopology = () => {
    const currentTopology = fingerprint();
    if (mode === 'parameters' && currentTopology !== topology) {
      const before = JSON.parse(topology), after = JSON.parse(currentTopology);
      const index = after.findIndex((entry,i) => JSON.stringify(entry) !== JSON.stringify(before[i]));
      throw new Error('Graph topology changed; reload or use workflow mode before exporting: ' +
        JSON.stringify({before:before[index],after:after[index]}));
    }
  };
  const validate = value => {
      if (typeof value === 'number' && (!Number.isFinite(value) ||
          (Number.isInteger(value) && !Number.isSafeInteger(value))))
        throw new Error('Number exceeds the safe JSON bridge range; snapshot was not saved');
      if (value && typeof value === 'object') Object.values(value).forEach(validate);
  };
  // Switching layers must not execute generation-time widget serializers.
  const draft = () => {
    checkTopology();
    const workflow = app.graph.serialize();
    validate(workflow);
    return {workflow, probe: describe()};
  };
  const snapshot = async () => {
    checkTopology();
    const result = await app.graphToPrompt();
    validate(result);
    return {workflow: result.workflow, output: result.output, probe: describe()};
  };
  const setMode = async value => {
    if (!['parameters', 'workflow'].includes(value)) throw new Error('Unsupported mode');
    if (mode === 'workflow' && value === 'parameters') topology = fingerprint();
    mode = value;
    style.disabled = mode === 'workflow';
    for (const key of Object.keys(originalFlags))
      graphCanvas[key] = mode === 'workflow' ? originalFlags[key] : false;
    if (mode === 'workflow') {
      hierarchy.root();
      for (let i=0;i<6;i++) { await nextFrame(); fitGraph(); }
    } else {
      const entries = hierarchy.walk();
      const entry = entries.find(e => JSON.stringify(e.path) === JSON.stringify(selectedPath)) ?? entries[0];
      if (entry) await focus(entry.path);
      else { selected = null; selectedPath = []; hierarchy.root(); graphCanvas.setDirty(true, true); }
    }
    return describe();
  };
  const prepareLoad = async () => {
    ++focusRevision;
    selected = null;
    selectedPath = [];
    document.querySelectorAll('[data-probe-widget]').forEach(e => e.removeAttribute('data-probe-widget'));
    // Finish native subgraph navigation before clearing the root graph. Otherwise
    // its pending hash watcher can restore an older workflow during configure.
    const router = document.getElementById('vue-app')?.__vue_app__?.config?.globalProperties?.$router;
    if (router && app.graph.id) await router.replace({hash: '#' + app.graph.id});
    hierarchy.root();
    await nextFrame();
  };
  const load = async workflow => {
    loading = true;
    try {
      await prepareLoad();
      await app.loadGraphData(workflow);
      topology = fingerprint();
      return await setMode(mode);
    } finally { loading = false; }
  };
  const sample = async types => {
    for (const type of types)
      if (!LiteGraph.registered_node_types[type]) throw new Error(`Custom node unavailable: ${type}`);
    loading = true;
    try {
      hierarchy.root();
      app.graph.clear();
      for (const [index, type] of types.entries()) {
        const node = LiteGraph.createNode(type);
        if (!node) throw new Error(`Custom node unavailable: ${type}`);
        node.pos = [100 + index * 400, 100];
        app.graph.add(node);
      }
      topology = fingerprint();
      selectedPath = [String(app.graph._nodes[0].id)];
      return await setMode(mode);
    } finally { loading = false; }
  };
  const loadApi = async prompt => {
    const missing = [...new Set(Object.values(prompt).map(n => n.class_type))]
      .filter(type => !LiteGraph.registered_node_types[type]);
    if (missing.length) throw new Error(`Custom nodes unavailable: ${missing.join(', ')}`);
    loading = true;
    try {
      await prepareLoad();
      await app.loadApiJson(prompt, 'Imported API workflow');
      selected = null;
      selectedPath = [];
      topology = fingerprint();
      return await setMode(mode);
    } finally { loading = false; }
  };
  window.addEventListener('resize', () => {
    if (loading) return;
    if (mode === 'workflow') fitGraph();
    else if (selected != null) focus(selectedPath).catch(console.error);
  });
  topology = fingerprint();
  window.loomstrokeProbe = {focus, describe, draft, snapshot, load, loadApi, sample, setMode};
  return {ready: true};
})()"""

PARAMETERS = r"""/* Restore published scalar widgets after load/Undo, using native callbacks. */
(() => {
  window.loomstrokeApplyValues = async entries => {
    const app = window.comfyAPI.app.app;
    const hierarchy = window.createProbeHierarchy(app, app.canvas);
    for (const entry of entries) {
      const node = hierarchy.walk().find(e => e.path.join(':') === String(entry.node_id))?.node;
      if (!node) throw new Error(`Published node not found: ${entry.node_id}`);
      const widget = node.widgets?.find(w => w.name === entry.input);
      if (!widget) throw new Error(`Published widget not found: ${entry.node_id}.${entry.input}`);
      if (JSON.stringify(widget.value) !== JSON.stringify(entry.value)) {
        widget.value = entry.value;
        widget.callback?.(entry.value, app.canvas, node);
      }
    }
    app.graph.setDirtyCanvas(true, true);
    return true;
  };
  return true;
})()"""
