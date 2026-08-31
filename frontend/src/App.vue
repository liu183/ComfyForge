<script setup>
import { ref, computed, onMounted } from 'vue'
import { getHealth, getCapabilities } from './api.js'
import GenerationPanel from './components/GenerationPanel.vue'
import TaskList from './components/TaskList.vue'

const health = ref(null)
const capabilities = ref([])
const activeType = ref('txt2img')
const loading = ref(true)
const error = ref('')

async function refreshMeta() {
  loading.value = true
  error.value = ''
  try {
    const [h, caps] = await Promise.all([getHealth(), getCapabilities()])
    health.value = h
    capabilities.value = caps
    if (!caps.some(c => c.type === activeType.value)) {
      activeType.value = caps[0]?.type || 'txt2img'
    }
  } catch (e) {
    error.value = String(e.message || e)
  } finally {
    loading.value = false
  }
}

const activeCap = computed(() =>
  capabilities.value.find(c => c.type === activeType.value) || null
)

const serverInfo = computed(() => {
  const s = health.value?.comfy_servers || []
  const reach = s.filter(x => x.reachable)
  return { total: s.length, reach: reach.length, nodes: s }
})

const typeLabels = {
  txt2img: '文生图',
  img2img: '图生图',
  txt2video: '文生视频',
  img2video: '图生视频'
}

onMounted(refreshMeta)
</script>

<template>
  <div class="app">
    <header class="topbar">
      <div class="brand">
        <span class="logo">◆</span>
        <h1>Comfy Service</h1>
        <span class="ver">v0.1</span>
      </div>
      <div class="sys-status">
        <span class="badge" :class="serverInfo.reach > 0 ? 'ok' : 'warn'">
          ComfyUI 节点 {{ serverInfo.reach }}/{{ serverInfo.total }}
        </span>
        <span v-if="health?.force_mock" class="badge warn">强制模拟模式</span>
        <span v-if="loading" class="muted">加载中…</span>
      </div>
    </header>

    <div v-if="error" class="banner err">{{ error }}</div>

    <nav class="tabs" v-if="capabilities.length">
      <button
        v-for="c in capabilities"
        :key="c.type"
        class="tab"
        :class="{ active: c.type === activeType, mock: c.backend === 'mock' }"
        @click="activeType = c.type"
      >
        {{ typeLabels[c.type] || c.label }}
        <em class="dot" :class="c.backend === 'mock' ? 'mock' : 'real'"></em>
      </button>
      <button class="tab refresh" @click="refreshMeta">刷新</button>
    </nav>

    <main class="layout">
      <section class="panel" v-if="activeCap">
        <GenerationPanel
          :capability="activeCap"
          @created="refreshMeta"
        />
      </section>
      <section class="panel">
        <TaskList />
      </section>
    </main>
  </div>
</template>

<style>
:root {
  --bg: #f4f6fa;
  --card: #ffffff;
  --line: #e2e8f0;
  --ink: #1e293b;
  --sub: #64748b;
  --accent: #2563eb;
  --ok: #16a34a;
  --warn: #d97706;
  --danger: #dc2626;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--ink); font-family: -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif; }
.app { max-width: 1200px; margin: 0 auto; padding: 16px; }
.topbar { display: flex; justify-content: space-between; align-items: center; padding: 12px 4px 16px; }
.brand { display: flex; align-items: center; gap: 10px; }
.brand .logo { color: var(--accent); font-size: 22px; }
.brand h1 { font-size: 20px; margin: 0; }
.ver { color: var(--sub); font-size: 12px; border: 1px solid var(--line); padding: 1px 8px; border-radius: 10px; }
.sys-status { display: flex; gap: 8px; align-items: center; }
.badge { font-size: 12px; padding: 3px 10px; border-radius: 12px; }
.badge.ok { background: #dcfce7; color: var(--ok); }
.badge.warn { background: #fef3c7; color: var(--warn); }
.muted { color: var(--sub); font-size: 12px; }
.banner { padding: 10px 14px; border-radius: 8px; margin-bottom: 12px; }
.banner.err { background: #fee2e2; color: var(--danger); }
.tabs { display: flex; gap: 8px; margin-bottom: 14px; flex-wrap: wrap; }
.tab { border: 1px solid var(--line); background: var(--card); color: var(--ink); padding: 8px 16px; border-radius: 8px; cursor: pointer; font-size: 14px; display: flex; align-items: center; gap: 6px; }
.tab.active { border-color: var(--accent); color: var(--accent); background: #eff6ff; font-weight: 600; }
.tab .dot { width: 8px; height: 8px; border-radius: 50%; }
.tab .dot.real { background: var(--ok); }
.tab .dot.mock { background: var(--warn); }
.tab.refresh { margin-left: auto; color: var(--sub); }
.layout { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; align-items: start; }
@media (max-width: 900px) { .layout { grid-template-columns: 1fr; } }
.panel { background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 18px; }
</style>
