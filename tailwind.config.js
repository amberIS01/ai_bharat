/** @type {import('tailwindcss').Config}
 * Day-14 demo build config. The standalone Tailwind CLI ships without
 * the official forms / typography plugins; the templates' use of those
 * is minimal (a few `<form>` widgets, no `prose` classes), and Tailwind's
 * core utilities cover the visual fidelity needed for the demo.
 */
module.exports = {
  content: [
    "./officer/templates/**/*.html",
    "./core/audit/templates/**/*.html",
  ],
  theme: {
    extend: {},
  },
  plugins: [],
}
