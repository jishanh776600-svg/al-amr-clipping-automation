import React from 'react'
import ReactDOM from 'react-dom/client'
import { createBrowserRouter, RouterProvider } from 'react-router-dom'

import { App } from './App'
import './index.css'
import { MissionControl } from './pages/MissionControl'
import { Ingest } from './pages/Ingest'
import { JobsList } from './pages/JobsList'
import { JobProgress } from './pages/JobProgress'
import { Review } from './pages/Review'
import { ClipGallery } from './pages/ClipGallery'
import { CampaignBuilder } from './pages/CampaignBuilder'
import { Publishing } from './pages/Publishing'
import { Settings } from './pages/Settings'

const router = createBrowserRouter([
  {
    path: '/',
    element: <App />,
    children: [
      { index: true, element: <MissionControl /> },
      { path: 'new', element: <Ingest /> },
      { path: 'jobs', element: <JobsList /> },
      { path: 'jobs/:jobId', element: <JobProgress /> },
      { path: 'jobs/:jobId/clips', element: <Review /> },
      { path: 'clips', element: <ClipGallery /> },
      { path: 'campaigns', element: <CampaignBuilder /> },
      { path: 'publishing', element: <Publishing /> },
      { path: 'settings', element: <Settings /> },
    ],
  },
])

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <RouterProvider router={router} />
  </React.StrictMode>,
)
