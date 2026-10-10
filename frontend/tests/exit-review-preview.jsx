import React from 'react'
import { createRoot } from 'react-dom/client'
import ExitReview from '../src/pages/cashier/ExitReview'
import '../src/index.css'
createRoot(document.getElementById('root')).render(<main className="max-w-6xl mx-auto p-6 space-y-4"><h1 className="text-2xl font-bold">Гарах уншилтын шалгалт — хиймэл өгөгдөл</h1><ExitReview siteId="synthetic" /></main>)
