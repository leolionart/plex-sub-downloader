/** Giữ y hệt config inline trước đây trong base.html (bản Tailwind Play CDN).
 *  DaisyUI KHÔNG chạy như plugin: dùng nguyên file full.min.css 4.12.23 (giống bản CDN cũ)
 *  để giữ đúng thứ tự cascade: daisyui.min.css → <style> trong base.html → tailwind.css. */
/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ['./app/templates/**/*.html'],
  theme: {
    extend: {
      colors: {
        plex: {
          gold: '#E5A00D',
          'gold-hover': '#CC8B00',
          dark: '#1F1F23',
          surface: '#282830',
          elevated: '#32323C',
        },
        primary: '#E5A00D',
        gold: '#E5A00D',
        'gold-hover': '#CC8B00',
        'bg-deepest': '#111113',
        'bg-app': '#1F1F23',
        'bg-surface': '#282830',
        'text-primary': '#EAEAEA',
        'text-secondary': '#A0A0A0',
        'text-muted': '#6A6A6A',
        'border-subtle': '#3A3A44',
        'border-default': '#44444E',
        'plex-gold': '#E5A00D',
        'plex-gold-hover': '#CC8B00',
      },
    },
  },
  plugins: [],
};
