<script setup>
import { ref, onMounted, onUnmounted, computed } from 'vue'
import { getTasks } from '../api.js'

const tasks = ref([])
const loading = ref(false)
let timer = null

async function refresh() {
  try {
    tasks.value = await getTasks()
  } catch { /* 静默 */ }
  loading.value = false
}

const statusClass = (s) => ({
  pending: 'pend', running: 'run', succeeded: 'ok', failed: 'err'
}[s] || 'pend')

const statusLabel = (s) => ({
  pending: '排队中', running: '生成中', succeeded: '完成', failed: '失败'
}[s] || s)

const typeLabel = (t) => ({
  txt2img: '文生图', img2img: '图生图', txt2video: '文生视频', img2video: '图生视频'
}[t] || t)

function openAsset(url) {
  window.open(url, '_blank')
}

onMounted(() => {
  refresh()
  timer = setInterval(refresh, 2500)
})
onUnmounted(() => clearInterval(timer))
</script>

<template>
  <div class="tl">
    <div class="thead">
      <h3>任务记录</h3>
      <button class="mini" @click="refresh">刷新</button>
    </div>

    <div v-if="!tasks.length" class="empty">暂无任务，在左侧发起一次生成吧</div>

    <ul class="list">
      <li v-for="t in tasks" :key="t.id" class="item" :class="statusClass(t.status)">
        <div class="row1">
          <span class="type">{{ typeLabel(t.type) }}</span>
          <span class="st" :class="statusClass(t.status)">{{ statusLabel(t.status) }}</span>
          <span class="backend" :class="{ mock: t.backend === 'mock' }">{{ t.backend }}</span>
        </div>
        <div class="row2">
          <span class="id">#{{ t.id }}</span>
          <span class="time">{{ new Date(t.created_at * 1000).toLocaleTimeString() }}</span>
        </div>
        <div v-if="t.error" class="errmsg">{{ t.error }}</div>
        <div v-if="t.result && t.result.assets.length" class="assets">
          <div
            v-for="(a, i) in t.result.assets"
            :key="i"
            class="asset"
            @click="openAsset(a.url)"
            :title="a.url"
          >
            <img v-if="a.kind === 'image'" :src="a.url" alt="result" />
            <img v-else-if="a.kind === 'video' || a.url.endsWith('.gif')" :src="a.url" alt="result" />
            <video v-else-if="a.kind === 'video'" :src="a.url" controls></video>
            <span v-if="a.note" class="note">{{ a.note }}</span>
          </div>
        </div>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.tl .thead { display: flex; justify-content: space-between; align-items: center; }
.tl h3 { margin: 0; font-size: 16px; }
.mini { border: 1px solid var(--line); background: #fff; padding: 4px 12px; border-radius: 6px; cursor: pointer; color: var(--sub); font-size: 12px; }
.empty { color: var(--sub); font-size: 13px; padding: 30px 0; text-align: center; }
.list { list-style: none; margin: 12px 0 0; padding: 0; display: flex; flex-direction: column; gap: 10px; }
.item { border: 1px solid var(--line); border-left: 3px solid #cbd5e1; border-radius: 8px; padding: 10px 12px; }
.item.ok { border-left-color: var(--ok); }
.item.err { border-left-color: var(--danger); }
.item.run { border-left-color: var(--accent); }
.row1 { display: flex; align-items: center; gap: 8px; }
.type { font-weight: 600; font-size: 14px; }
.st { font-size: 12px; padding: 1px 8px; border-radius: 10px; }
.st.ok { background: #dcfce7; color: var(--ok); }
.st.err { background: #fee2e2; color: var(--danger); }
.st.run { background: #dbeafe; color: var(--accent); }
.st.pend { background: #f1f5f9; color: var(--sub); }
.backend { font-size: 11px; background: #f1f5f9; color: var(--sub); padding: 1px 8px; border-radius: 10px; margin-left: auto; }
.backend.mock { background: #fef3c7; color: var(--warn); }
.row2 { display: flex; gap: 10px; margin-top: 4px; color: var(--sub); font-size: 12px; }
.errmsg { margin-top: 6px; font-size: 12px; color: var(--danger); background: #fee2e2; padding: 5px 8px; border-radius: 6px; }
.assets { display: flex; gap: 10px; margin-top: 8px; flex-wrap: wrap; }
.asset { position: relative; cursor: pointer; }
.asset img { max-width: 130px; max-height: 130px; border-radius: 6px; border: 1px solid var(--line); display: block; }
.asset video { max-width: 130px; border-radius: 6px; }
.asset .note { position: absolute; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.6); color: #fff; font-size: 10px; padding: 2px 4px; border-radius: 0 0 6px 6px; }
</style>
