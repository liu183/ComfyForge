<script setup>
import { ref, reactive, computed, watch } from 'vue'
import { createTask, createTaskWithImage } from '../api.js'

const props = defineProps({
  capability: { type: Object, required: true }
})
const emit = defineEmits(['created'])

const form = reactive({})
const imageFile = ref(null)
const imagePreview = ref('')
const submitting = ref(false)
const feedback = ref('')

// capability 切换（仅当类型变化）时重建表单，避免提交后刷新重置已填内容
watch(() => props.capability?.type, (t) => {
  if (t) resetForm(props.capability)
}, { immediate: true })

function resetForm(cap) {
  for (const k of Object.keys(form)) delete form[k]
  imageFile.value = null
  imagePreview.value = ''
  feedback.value = ''
  for (const p of cap.params) {
    if (p.type === 'image') continue
    form[p.name] = p.default ?? ''
  }
}

const acceptsImage = computed(() => props.capability.accepts_image)
const missingRequired = computed(() => {
  return props.capability.params.filter(p =>
    p.required && (p.type === 'image'
      ? !imageFile.value
      : (form[p.name] === undefined || form[p.name] === '' || form[p.name] === null))
  )
})

function onFile(e) {
  const f = e.target.files[0]
  if (!f) return
  imageFile.value = f
  imagePreview.value = URL.createObjectURL(f)
}

async function submit() {
  const miss = missingRequired.value
  if (miss.length) {
    feedback.value = '请填写必填项: ' + miss.map(p => p.label).join('、')
    return
  }
  submitting.value = true
  feedback.value = ''
  try {
    const params = { ...form }
    for (const p of props.capability.params) {
      if (params[p.name] === '' || params[p.name] === undefined) continue
      if (p.type === 'int') params[p.name] = Number(params[p.name])
      else if (p.type === 'float') params[p.name] = Number(params[p.name])
      else if (p.type === 'bool') params[p.name] = !!params[p.name]
    }
    let task
    if (acceptsImage.value && imageFile.value) {
      task = await createTaskWithImage(props.capability.type, params, imageFile.value)
    } else {
      task = await createTask(props.capability.type, params)
    }
    feedback.value = `已提交任务 ${task.id}（${task.backend}），正在生成…`
    emit('created', task)
  } catch (e) {
    feedback.value = '提交失败: ' + String(e.message || e)
  } finally {
    submitting.value = false
  }
}

function inputType(p) {
  return p.type === 'float' ? 'number' : 'number'
}
</script>

<template>
  <div class="gen">
    <div class="head">
      <h2>{{ capability.label }}</h2>
      <span class="mode" :class="capability.backend === 'mock' ? 'mock' : 'real'">
        {{ capability.backend === 'mock' ? '模拟模式' : '真实生成' }} · {{ capability.backend }}
      </span>
    </div>
    <p class="desc">{{ capability.description }}</p>
    <p class="reason" v-if="capability.reason">ⓘ {{ capability.reason }}</p>

    <div class="fields">
      <label v-for="p in capability.params" :key="p.name" class="field" :class="{ req: p.required }">
        <span class="flabel">
          {{ p.label }}<i v-if="p.required">*</i>
          <em v-if="p.type === 'image'">上传图片</em>
        </span>

        <!-- 图片上传 -->
        <div v-if="p.type === 'image'" class="imgpick" @click="$refs['f' + p.name].click()">
          <img v-if="imagePreview" :src="imagePreview" alt="preview" />
          <span v-else>点击选择图片</span>
          <input :ref="'f' + p.name" type="file" accept="image/*" hidden @change="onFile" />
        </div>

        <!-- 下拉选择 -->
        <select v-else-if="p.type === 'select'" v-model="form[p.name]">
          <option v-for="o in p.options" :key="o" :value="o">{{ o }}</option>
        </select>

        <!-- 布尔 -->
        <div v-else-if="p.type === 'bool'" class="boolrow">
          <input type="checkbox" v-model="form[p.name]" />
        </div>

        <!-- 数字 -->
        <input
          v-else-if="p.type === 'int' || p.type === 'float'"
          type="number"
          :step="p.type === 'float' ? 'any' : 1"
          :min="p.min"
          :max="p.max"
          v-model="form[p.name]"
        />

        <!-- 文本 -->
        <textarea
          v-else
          :rows="p.name === 'prompt' ? 3 : 1"
          v-model="form[p.name]"
          :placeholder="p.description"
        ></textarea>

        <small v-if="p.description && p.type !== 'text'">{{ p.description }}</small>
      </label>
    </div>

    <button class="submit" :disabled="submitting" @click="submit">
      {{ submitting ? '提交中…' : '开始生成' }}
    </button>
    <p v-if="feedback" class="feedback" :class="{ err: feedback.includes('失败') || feedback.includes('必填') }">{{ feedback }}</p>
  </div>
</template>

<style scoped>
.gen .head { display: flex; align-items: center; justify-content: space-between; }
.gen h2 { margin: 0; font-size: 17px; }
.mode { font-size: 12px; padding: 2px 10px; border-radius: 12px; }
.mode.real { background: #dcfce7; color: var(--ok); }
.mode.mock { background: #fef3c7; color: var(--warn); }
.desc { color: var(--sub); font-size: 13px; margin: 8px 0 0; }
.reason { font-size: 12px; color: var(--warn); background: #fffbeb; border: 1px dashed #fde68a; padding: 6px 10px; border-radius: 6px; }
.fields { display: flex; flex-direction: column; gap: 12px; margin: 14px 0; }
.field { display: flex; flex-direction: column; gap: 5px; }
.flabel { font-size: 13px; font-weight: 600; }
.flabel i { color: var(--danger); font-style: normal; margin-left: 2px; }
.flabel em { font-style: normal; color: var(--sub); font-weight: 400; margin-left: 8px; font-size: 12px; }
.field input[type=text], .field input[type=number], .field select, .field textarea {
  border: 1px solid var(--line); border-radius: 8px; padding: 8px 10px; font-size: 14px; font-family: inherit; outline: none; background: #fff;
}
.field input:focus, .field select:focus, .field textarea:focus { border-color: var(--accent); }
.field textarea { resize: vertical; }
.field small { color: var(--sub); font-size: 12px; }
.imgpick { border: 1px dashed #cbd5e1; border-radius: 8px; height: 90px; display: flex; align-items: center; justify-content: center; color: var(--sub); cursor: pointer; overflow: hidden; background: #f8fafc; }
.imgpick img { max-height: 90px; width: auto; }
.boolrow { padding-top: 4px; }
.submit { width: 100%; background: var(--accent); color: #fff; border: none; border-radius: 8px; padding: 11px; font-size: 15px; cursor: pointer; font-weight: 600; }
.submit:disabled { opacity: 0.6; cursor: not-allowed; }
.feedback { font-size: 13px; margin-top: 10px; color: var(--ok); }
.feedback.err { color: var(--danger); }
</style>
