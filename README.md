ABSTRACT 

Cardiovascular diseases (CVDs) cause approximately 18 million annual deaths, yet 
conventional diagnostic tools like Holter monitors are often reactive and lack real 
time clinical interpretation. This thesis presents an AI driven platform that 
integrates IoMT technologies with Large Language Models (LLMs) to deliver 
continuous, human centric heart health monitoring.  

The methodology employs an AD8232 analog front end and a high performance 
ESP32 microcontroller to acquire single lead ECG signals at a medical grade 
sampling rate of 360 Hz. Digitized data is transmitted via Bluetooth Low Energy 
(BLE) using a custom binary protocol to a cross platform Flutter based mobile 
application for instantaneous waveform visualization. The application synchronizes 
buffered data with a cloud hosted PostgreSQL database, where an automated 
Python based background service performs physiological feature extraction, 
including precise R peak detection and heart rate variability (HRV) metrics. These 
extracted features are subsequently analysed by a Model Context Protocol (MCP) 
server utilizing a dual prompt LLM framework to generate both a quantitative 
cardiovascular health score (0–10) and a detailed, patient friendly narrative 
summary. Results demonstrate an efficient pipeline maintaining low latency of 
approximately 33 milliseconds per packet while offloading intensive processing 
tasks from mobile edge devices. Ultimately, this research provides a scalable and 
interpretable solution for proactive and accessible cardiovascular care.
