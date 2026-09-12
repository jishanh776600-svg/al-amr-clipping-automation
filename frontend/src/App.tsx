import { useEffect, useState } from 'react'
import { NavLink, Outlet } from 'react-router-dom'

import { api, getStoredToken, onAuthChange, type SystemStatus } from './api'
import { AuthModal } from './components/AuthModal'

/**
 * Application shell.
 *
 * A masthead rather than a sidebar: this is a four-screen tool, and a
 * persistent nav rail would spend a fifth of the width restating that.
 */
export function App() {
  const [system, setSystem] = useState<SystemStatus | null>(null)
  const [hasToken, setHasToken] = useState<boolean>(() => Boolean(getStoredToken()))
  const [isAuthModalOpen, setIsAuthModalOpen] = useState(false)

  const checkSystem = () => {
    api.system().then(setSystem).catch(() => setSystem(null))
  }

  useEffect(() => {
    checkSystem()
    const unsub = onAuthChange((token) => {
      setHasToken(Boolean(token))
      checkSystem()
    })
    return unsub
  }, [])

  return (
    <div className="min-h-screen bg-ink-900">
      <header className="border-b border-ink-800">
        <div className="mx-auto flex max-w-[1600px] items-baseline gap-8 px-6 py-4 lg:px-10">
          <NavLink to="/" className="group flex items-baseline gap-2.5">
            <span className="font-display text-2xl leading-none text-ink-100">
              AL AMR <span className="text-sm font-sans font-normal text-ink-400">· Auto<span className="italic text-sodium-500">Clip</span></span>
            </span>
          </NavLink>

          <nav className="flex flex-wrap items-baseline gap-5 md:gap-7">
            <TopLink to="/" end>
              Overview
            </TopLink>
            <TopLink to="/new">
              New Ingest
            </TopLink>
            <TopLink to="/jobs">
              Jobs
            </TopLink>
            <TopLink to="/clips">
              Clips
            </TopLink>
            <TopLink to="/campaigns">
              Campaigns
            </TopLink>
            <TopLink to="/publishing">
              Publishing
            </TopLink>
            <TopLink to="/settings">
              Settings
            </TopLink>
          </nav>

          <div className="ml-auto flex items-center gap-4">
            <button
              onClick={() => setIsAuthModalOpen(true)}
              className={`flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-semibold transition ${
                hasToken
                  ? 'border border-emerald-500/40 bg-emerald-500/10 text-emerald-400 hover:bg-emerald-500/20'
                  : 'border border-sodium-500/50 bg-sodium-500/15 text-sodium-400 hover:bg-sodium-500/25 animate-pulse'
              }`}
              title={hasToken ? 'Operator token configured. Click to manage.' : 'Authentication required for remote operations. Click to connect.'}
            >
              <span className={hasToken ? 'text-emerald-400' : 'text-sodium-400'}>
                {hasToken ? '●' : '🔑'}
              </span>
              <span>{hasToken ? 'Authenticated' : 'Enter Token'}</span>
            </button>

            {system && <SystemBadge system={system} />}
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-[1600px] px-6 pb-24 lg:px-10">
        <Outlet />
      </main>

      <AuthModal
        isOpen={isAuthModalOpen}
        onClose={() => setIsAuthModalOpen(false)}
        onSuccess={() => checkSystem()}
      />
    </div>
  )
}

function TopLink({
  to,
  end,
  children,
}: {
  to: string
  end?: boolean
  children: React.ReactNode
}) {
  return (
    <NavLink
      to={to}
      end={end}
      className={({ isActive }) =>
        [
          'text-sm font-medium transition-colors duration-200',
          isActive ? 'text-sodium-500' : 'text-ink-400 hover:text-ink-200',
        ].join(' ')
      }
    >
      {children}
    </NavLink>
  )
}

/**
 * Compact machine status.
 *
 * Surfaced permanently rather than hidden in settings because the two things it
 * reports — whether ffmpeg can render, and whether the GPU is being used — are
 * the two that change how long everything takes.
 */
function SystemBadge({ system }: { system: SystemStatus }) {
  const accel = system.accel.toUpperCase()
  return (
    <div className="hidden items-baseline gap-4 text-xs md:flex">
      <span className="numeric text-ink-400">
        {accel}
        {system.gpu_name && accel === 'CUDA' && (
          <span className="text-ink-600"> · {system.gpu_name.replace('NVIDIA GeForce ', '')}</span>
        )}
      </span>
      <span
        className={system.ready ? 'text-ink-400' : 'text-signal-bad'}
        title={system.ready ? 'All required components present' : 'Run autoclip doctor'}
      >
        {system.ready ? 'ready' : 'not ready'}
      </span>
    </div>
  )
}
