# PortfolioPilot Design System

This document outlines the design language, color palette, and component structure for **PortfolioPilot**, a premium portfolio analysis dashboard.

This file serves as context for Google Stitch agents to generate cohesive layouts and components matching the existing application.

## 1. Visual Language & Principles

**Vibe:** Premium, Data-heavy but spacious, High-Contrast Dark Mode, Glassmorphism, "Bloomberg Terminal meets Apple/Stripe design".
**Key principles:**
- **Dark Mode First:** Deep, sophisticated blue/gray backgrounds, not pure black. 
- **Glassmorphism:** Cards and navigational elements use semi-transparent backgrounds with a subtle backdrop blur to give depth.
- **Accents:** Neon-inspired blue, purple, and cyan gradients to highlight important values or active states.
- **Data Clarity:** Data is sacred. Numbers must be easy to read (monospace digits or tabular nums if possible), with clear positive (green) and negative (red) indication.

## 2. Color Palette (CSS Variables)

The entire application uses CSS variables defined on `:root`. Stick to these definitions when generating code.

### Backgrounds
- `--bg-primary: #0a0e17;` (Deepest background)
- `--bg-secondary: #111827;`
- `--bg-card: #1a2035;` (Base for all panels/cards)
- `--bg-card-hover: #1f2847;`
- `--bg-glass: rgba(26, 32, 53, 0.75);` (Used with `backdrop-filter: blur(20px)`)

### Borders
- `--border: rgba(255, 255, 255, 0.06);` (Subtle dividers)
- `--border-light: rgba(255, 255, 255, 0.1);`

### Typography (Colors)
- `--text-primary: #f1f5f9;` (Headings, primary values)
- `--text-secondary: #94a3b8;` (Standard text, labels)
- `--text-muted: #64748b;` (Secondary labels, tiny hints)

### Accents & Gradients
- `--accent-blue: #3b82f6;`
- `--accent-purple: #8b5cf6;`
- `--accent-cyan: #06b6d4;`
- Use gradients like: `linear-gradient(135deg, var(--accent-blue), var(--accent-purple))` for active states or the logo.

### Semantic Status Colors
- **Positive (Buy/Profit):**
  - `--green: #22c55e;`
  - `--green-soft: rgba(34, 197, 94, 0.12);` (Background for badges)
- **Warning (Hold/Neutral):**
  - `--yellow: #eab308;`
  - `--yellow-soft: rgba(234, 179, 8, 0.12);`
- **Negative (Sell/Loss):**
  - `--red: #ef4444;`
  - `--red-soft: rgba(239, 68, 68, 0.12);`

## 3. Typography & Spacing

**Font Family:** 
`var(--font)` -> `'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;`
Ensure weights used are 400 (regular), 500 (medium for labels), 600 (semibold for buttons/smaller headings), and 800 (extra bold for massive portfolio numbers).

**Border Radius:**
- `--radius-sm: 8px;` (Buttons, small badges)
- `--radius-md: 12px;` (Inputs, smaller cards)
- `--radius-lg: 16px;` (Main cards, modals)
- `--radius-xl: 20px;` (Surrounding layout containers if any)

**Shadows:**
Use shadows to create elevation against the dark background.
- `--shadow-sm: 0 2px 8px rgba(0, 0, 0, 0.3);`
- `--shadow-md: 0 4px 20px rgba(0, 0, 0, 0.4);`
- `--shadow-lg: 0 8px 40px rgba(0, 0, 0, 0.5);`
- `--shadow-glow: 0 0 30px rgba(59, 130, 246, 0.15);` (For active tabs or highlighted buttons)

## 4. Components

### Cards (The standard container)
All widget content lives inside cards with the following setup:
```css
background: var(--bg-card);
border: 1px solid var(--border);
border-radius: var(--radius-lg);
padding: 1.5rem;
box-shadow: var(--shadow-sm);
```

### Stats / Badges
Status badges (e.g., "Buy", "Hold", "Sell") use a pill-shape (`border-radius: 99px`), the soft background color variant (e.g., `--green-soft`), and the solid text color variant (`--green`).

### Buttons
Primary buttons use `--accent-blue` or gradients. They should have a hover effect that slightly translates them up (`transform: translateY(-2px)`) and adds a subtle glow (`box-shadow: var(--shadow-glow)`).

## 5. Layout Defaults
- The main app dashboard uses CSS Grid (`display: grid`) to arrange cards responsively.
- The top header is sticky (`position: sticky; top: 0;`), utilizing `--bg-glass` for the backdrop filter blur effect.
- Interactions should feel snappy but smooth, utilizing `--transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);`.
