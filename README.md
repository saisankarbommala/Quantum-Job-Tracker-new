# ⚛️ Quantum Job Tracker

[![Flask](https://img.shields.io/badge/Flask-2.3+-blue.svg?logo=flask)](https://flask.palletsprojects.com/)
[![Qiskit](https://img.shields.io/badge/Qiskit-Quantum-purple.svg?logo=ibm)](https://qiskit.org/)
[![Python](https://img.shields.io/badge/Python-3.9+-yellow.svg?logo=python)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Status](https://img.shields.io/badge/Status-Active-success.svg)](#)

> **A Flask-powered full-stack application to manage, monitor, and analyze quantum computing jobs on IBM Quantum & IBM Cloud — powered by Qiskit Runtime.**

---

## 📖 Table of Contents
- [Overview](#-overview)
- [Features](#-features)
- [Technology Stack](#-technology-stack)
- [Application Pages](#-application-pages)
- [Installation & Setup](#-installation--setup)
- [Usage Guide](#-usage-guide)
- [Export & Integration](#-export--integration)
- [Future Enhancements](#-future-enhancements)
- [License](#-license)

---

## 🌌 Overview

**Quantum Job Tracker** provides a unified dashboard to **track backend performance, manage job lifecycles, and visualize analytics** for IBM Quantum systems.  
It connects seamlessly to both **public** and **private IBM Quantum backends**, offering **real-time monitoring**, **backend recommendations**, and **intelligent analytics**.

---

## ✨ Features

### 🔹 Job & Execution Management
- **🔐 Secure Authentication** — Login using IBM Quantum API Token (encrypted via **Fernet** and stored securely in session).  
- **☁️ Dual Service Support** — Connect to both Public and Private IBM Quantum/Cloud instances via **CRN (Cloud Resource Name)**.  
- **⏱️ Real-time Job Monitoring** — Track job statuses (*Queued, Running, Completed, Failed*) with color-coded badges.  
- **⚙️ Flexible Job Submission**
  - **Sampler Mode:** For measurement counts  
  - **Estimator Mode:** For expectation values (Pauli observables, VQE, etc.)  
  - **AerSimulator:** Local testing and debugging  
- **🏷️ Job Tagging** — Auto-tag with `user:<user_id>` and priority for quick filtering.  
- **❌ Job Cancellation** — Instantly cancel queued or running jobs.

---

### 🧠 Backend & Data Analytics
- **🔧 Hardware Insights** — View configuration (max shots, basis gates) and calibration data (T1, T2, readout error).  
- **💡 Smart Recommendations** — Backend recommender ranks top 3 choices using qubit count, queue time, and simulator availability.  
- **📊 Historical Analytics** — Powered by **Chart.js**, visualizing:
  - Job status distribution  
  - Backend usage trends  
  - T1/T2 correlation  
  - Gate error rate comparison  
- **⏳ Queue Time Prediction** — Learns from historical runtime data to estimate average wait and predicted start times.

---

## 💻 Technology Stack

| **Category** | **Technology** | **Purpose** |
|---------------|----------------|--------------|
| **Backend Framework** | Python, Flask | Routing, request handling, core logic |
| **Quantum SDK** | Qiskit Runtime | Interface for job submission, backend data, and runtime access |
| **Security / Auth** | Fernet (Cryptography), SHA256, Flask-Limiter | Token encryption, API rate limiting |
| **Data Persistence** | JSON Files | Local caching and history (`queue_history.json`, `job_history.json`) |
| **Frontend** | Bootstrap 5, Jinja2 | Responsive UI and templating |
| **Visualization** | Chart.js | Dynamic chart generation for analytics pages |
| **Utilities** | Threading, Requests | Background polling and Slack integration |

---

## 🌐 Application Pages

### 🏠 **Dashboard** (`/dashboard`)
- Summarizes all **available quantum backends** and highlights the **least busy queue**.
- Displays **public and private backends** with real-time statuses.
- Shows **Top 3 Recommended Backends** based on scoring algorithm.

---

### 🧾 **Jobs** (`/jobs`)
- **Filtering:** Filter jobs by Backend, Status, User ID, or Tags.  
- **CRN Management:** Add or clear IBM Cloud CRN credentials.  
- **Quick Actions:** Cancel or view job details directly from the list.

---

### 🔍 **Job Details** (`/jobs/<job_id>`)
- **Lifecycle Timeline:** Queued → Running → Completed visualization.  
- **Results Display:**  
  - Measurement Counts with probability bars  
  - Estimator outputs or raw result data  

---

### ⚙️ **Backend Details** (`/backends/<name>`)
- Displays backend configuration and calibration metrics (T1, T2, readout error).  
- Shows **hardware health** and **operational readiness**.

---

### 📉 **Backend Analytics** (`/backends/<name>/analytics`)
- Dedicated analytics dashboard for individual devices.  
- Visualizes **T1–T2 correlations**, **error rates**, and **historical stability**.  
- Includes **filters for Time Range, Qubit Selection, and Metric Type**.

