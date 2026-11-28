import { defineConfig } from "vite";

// สัญญากับ deploy (cloud/infra/deploy.py + CloudFront):
// - build ออกที่ web/dist, base "./" ให้เปิดจาก S3/CloudFront path ใดก็ได้
// - ไฟล์ใน dist/assets/ มี hash ในชื่อเสมอ (CloudFront ตั้ง cache แบบ immutable)
// - public/config.json และ public/data/buildings.geojson ถูกคัดลอกไปตามเดิม และอ่านตอน runtime
export default defineConfig({
  base: "./",
  build: {
    outDir: "dist",
    sourcemap: false,
    // ไลบรารีแผนที่/กราฟแยก chunk: แก้โค้ดเว็บแล้วผู้ใช้ไม่ต้องโหลดไลบรารีใหม่
    chunkSizeWarningLimit: 1100,
    rollupOptions: {
      output: {
        manualChunks: {
          maplibre: ["maplibre-gl"],
          echarts: ["echarts"],
        },
      },
    },
  },
});
