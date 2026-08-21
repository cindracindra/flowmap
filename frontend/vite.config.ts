import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const config = JSON.parse(
  fs.readFileSync(path.join(projectRoot, 'flowmap.config.json'), 'utf8'),
) as { outputDir: string; classFiles?: Record<string, string> }
const outputDir = path.resolve(projectRoot, config.outputDir)

function flowmapData(): Plugin {
  const virtualId = 'virtual:flowmap-data'
  const resolvedId = `\0${virtualId}`
  return {
    name: 'flowmap-data',
    resolveId(id) {
      return id === virtualId ? resolvedId : undefined
    },
    load(id) {
      if (id !== resolvedId) return undefined
      const jsonImport = (name: string) => JSON.stringify(path.join(outputDir, `${name}.json`))
      return [
        `export const classFilesRaw = ${JSON.stringify(config.classFiles ?? {})};`,
        `export { default as fullGraphRaw } from ${jsonImport('full_cfg')};`,
        `export { default as topicClusterRaw } from ${jsonImport('topic_cluster')};`,
        `export { default as topicOperationsRaw } from ${jsonImport('topic_operations')};`,
        `export { default as opseqVisualisationsRaw } from ${jsonImport('opseq_visualisations')};`,
      ].join('\n')
    },
  }
}

export default defineConfig({
  plugins: [react(), flowmapData()],
})
