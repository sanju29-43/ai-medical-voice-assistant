/// <reference types="vite/client" />
import { useState, useEffect, useRef } from 'react';
import { Phone, PhoneOff, Database, Calendar, Award, User, Clock, ShieldCheck, Activity } from 'lucide-react';

const API_URL = import.meta.env.VITE_API_URL || '';

type ConnectionStatus = 'Ready' | 'Connecting...' | 'Listening...' | 'Thinking...' | 'Speaking...' | 'Appointment confirmed' | 'Call ended' | 'Error';

export default function App() {
  const [status, setStatus] = useState<ConnectionStatus>('Ready');
  const [sttProvider, setSttProvider] = useState<'deepgram' | 'elevenlabs' | 'sarvam'>('deepgram');
  const [ttsProvider, setTtsProvider] = useState<'deepgram' | 'elevenlabs' | 'sarvam'>('sarvam');
  const [selectedLanguage, setSelectedLanguage] = useState<'en' | 'hi' | 'kn'>('en');
  const [variables, setVariables] = useState<Array<{ key: string; value: string }>>([
    { key: 'patient_name', value: '' },
    { key: 'doctor_name', value: '' }
  ]);
  const [newKey, setNewKey] = useState('');
  const [newValue, setNewValue] = useState('');
  const [activeSessionId, setActiveSessionId] = useState<string>('');

  // Real-time extracted appointment state
  const [appointmentInfo, setAppointmentInfo] = useState<{
    id: string;
    doctor: string;
    specialty: string;
    date: string;
    time: string;
    status: string;
  }>({
    id: '—',
    doctor: '—',
    specialty: '—',
    date: '—',
    time: '—',
    status: '—'
  });

  const wsRef = useRef<WebSocket | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const micStreamRef = useRef<MediaStream | null>(null);
  const nextPlayTimeRef = useRef<number>(0);
  const speakingTimeoutRef = useRef<any>(null);
  const lastKnownAppointmentIdRef = useRef<number>(0);

  const playPcmAudio = (arrayBuffer: ArrayBuffer) => {
    if (!audioContextRef.current) return;
    const ctx = audioContextRef.current;
    
    const int16Array = new Int16Array(arrayBuffer);
    const float32Array = new Float32Array(int16Array.length);
    for (let i = 0; i < int16Array.length; i++) {
      float32Array[i] = int16Array[i] / 32768.0;
    }
    
    const audioBuffer = ctx.createBuffer(1, float32Array.length, 16000);
    audioBuffer.getChannelData(0).set(float32Array);
    
    const source = ctx.createBufferSource();
    source.buffer = audioBuffer;
    source.connect(ctx.destination);
    
    const currentTime = ctx.currentTime;
    let playTime = nextPlayTimeRef.current;
    if (playTime < currentTime) {
      playTime = currentTime;
    }
    
    source.start(playTime);
    nextPlayTimeRef.current = playTime + audioBuffer.duration;
  };

  // Poll database for recently confirmed appointments in background to showcase real-time sync
  useEffect(() => {
    const pollInterval = setInterval(async () => {
      if (status !== 'Ready') {
        try {
          const res = await fetch(API_URL + '/api/appointments/latest');
          if (res.ok) {
            const data = await res.json();
            if (data.status !== 'none' && data.id > lastKnownAppointmentIdRef.current) {
              setAppointmentInfo({
                id: data.id ? `APT-${data.id}` : '—',
                doctor: data.doctor,
                specialty: data.specialty,
                date: data.date,
                time: data.time,
                status: data.status
              });
            }
          }
        } catch (e) {
          // ignore
        }
      }
    }, 2000);
    return () => clearInterval(pollInterval);
  }, [status]);

  const resetAppointmentPanel = async () => {
    setAppointmentInfo({
      id: '—',
      doctor: '—',
      specialty: '—',
      date: '—',
      time: '—',
      status: '—'
    });
    try {
      const res = await fetch(API_URL + '/api/appointments/latest');
      if (res.ok) {
        const data = await res.json();
        lastKnownAppointmentIdRef.current = data.id || 0;
      }
    } catch (e) {
      lastKnownAppointmentIdRef.current = 0;
    }
  };

  // Start Call Session (handles WebSocket based on backend environment settings)
  const startCall = async () => {
    setStatus('Connecting...');
    await resetAppointmentPanel();
    try {
      // 1. Create a voice session by calling backend session endpoint
      const varMap: Record<string, string> = {};
      variables.forEach(v => {
        if (v.key) varMap[v.key] = v.value;
      });
      const response = await fetch(API_URL + '/api/voice/session', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          language: selectedLanguage,
          stt_provider: sttProvider,
          tts_provider: ttsProvider,
          variables: varMap
        })
      });
      const data = await response.json();
      
      if (!response.ok) throw new Error(data.detail || 'Failed to start session');
      setActiveSessionId(data.session_id);

      // Direct local/remote WebSocket transport flow
      const ws = new WebSocket(data.ws_url);
      wsRef.current = ws;

      ws.onopen = async () => {
        setStatus('Listening...');
        try {
          const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
          micStreamRef.current = stream;
          audioContextRef.current = new AudioContext({ sampleRate: 16000 });
          const source = audioContextRef.current.createMediaStreamSource(stream);
          
          const processor = audioContextRef.current.createScriptProcessor(4096, 1, 1);
          source.connect(processor);
          processor.connect(audioContextRef.current.destination);

          processor.onaudioprocess = (e) => {
            if (ws.readyState === WebSocket.OPEN) {
              const inputData = e.inputBuffer.getChannelData(0);
              const pcmData = new Int16Array(inputData.length);
              for (let i = 0; i < inputData.length; i++) {
                pcmData[i] = Math.max(-1, Math.min(1, inputData[i])) * 0x7FFF;
              }
              ws.send(pcmData.buffer);
            }
          };
        } catch (e) {
          console.error('Mic access denied:', e);
          setStatus('Error');
          stopCall();
        }
      };

      ws.onmessage = async (e) => {
        try {
          if (e.data instanceof Blob) {
            setStatus('Speaking...');
            if (speakingTimeoutRef.current) clearTimeout(speakingTimeoutRef.current);
            speakingTimeoutRef.current = setTimeout(() => {
              setStatus('Listening...');
            }, 1500);
            const arrayBuffer = await e.data.arrayBuffer();
            playPcmAudio(arrayBuffer);
          } else {
            const msg = JSON.parse(e.data);
            if (msg.type === 'appointment_extracted') {
              setAppointmentInfo(msg.data);
            } else if (msg.type === 'variable_extracted') {
              const allVars = msg.all_variables || {};
              const list = Object.keys(allVars).map(k => ({ key: k, value: allVars[k] }));
              setVariables(list);
            }
          }
        } catch (err) {
          // ignore
        }
      };

      ws.onclose = () => {
        setStatus('Call ended');
        stopCall();
      };

      ws.onerror = () => {
        setStatus('Error');
        stopCall();
      };
    } catch (err: any) {
      console.error(err);
      setStatus('Error');
    }
  };

  const stopCall = () => {
    if (speakingTimeoutRef.current) {
      clearTimeout(speakingTimeoutRef.current);
      speakingTimeoutRef.current = null;
    }

    // Stop WebSockets
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
    // Stop audio streams
    if (micStreamRef.current) {
      micStreamRef.current.getTracks().forEach(track => track.stop());
      micStreamRef.current = null;
    }
    if (audioContextRef.current) {
      audioContextRef.current.close();
      audioContextRef.current = null;
    }
    setStatus('Ready');
    setActiveSessionId('');
    resetAppointmentPanel();
    const resetVars = variables.map(v => ({ key: v.key, value: '' }));
    setVariables(resetVars);
  };


  return (
    <div className="min-h-screen bg-gray-50 flex flex-col items-center justify-center p-4">
      {/* Container */}
      <div className="w-full max-w-4xl bg-white rounded-3xl shadow-xl overflow-hidden grid grid-cols-1 md:grid-cols-12">
        
        {/* Left Control Panel */}
        <div className="md:col-span-7 p-8 md:p-12 flex flex-col justify-between border-b md:border-b-0 md:border-r border-gray-100">
          <div>
            <div className="flex items-center space-x-2 text-blue-600 font-semibold mb-2">
              <Activity className="h-5 w-5 animate-pulse" />
              <span className="text-sm tracking-wider uppercase">Your Virtual Receptionist</span>
            </div>
            <h1 className="text-2xl font-bold text-gray-900 mb-4">🏥 AI MEDICAL ASSISTANT</h1>
            {/* Agent Settings Form */}
            <div className="space-y-4 mb-6 bg-gray-50 p-6 rounded-2xl">
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-xs font-semibold uppercase tracking-wider text-gray-400 mb-1">STT Provider</label>
                  <select
                    value={sttProvider}
                    onChange={(e) => setSttProvider(e.target.value as any)}
                    className="w-full px-3 py-2 bg-white rounded-lg border border-gray-200 text-sm font-medium text-gray-700 focus:outline-none focus:ring-2 focus:ring-blue-500"
                  >
                    <option value="deepgram">Deepgram</option>
                    <option value="elevenlabs">ElevenLabs</option>
                    <option value="sarvam">Sarvam AI</option>
                  </select>
                </div>
                <div>
                  <label className="block text-xs font-semibold uppercase tracking-wider text-gray-400 mb-1">TTS Provider</label>
                  <select
                    value={ttsProvider}
                    onChange={(e) => setTtsProvider(e.target.value as any)}
                    className="w-full px-3 py-2 bg-white rounded-lg border border-gray-200 text-sm font-medium text-gray-700 focus:outline-none focus:ring-2 focus:ring-blue-500"
                  >
                    <option value="sarvam">Sarvam AI</option>
                    <option value="deepgram">Deepgram</option>
                    <option value="elevenlabs">ElevenLabs</option>
                  </select>
                </div>
              </div>

              <div>
                <label className="block text-xs font-semibold uppercase tracking-wider text-gray-400 mb-1">Language</label>
                <select
                  value={selectedLanguage}
                  onChange={(e) => setSelectedLanguage(e.target.value as any)}
                  className="w-full px-3 py-2 bg-white rounded-lg border border-gray-200 text-sm font-medium text-gray-700 focus:outline-none focus:ring-2 focus:ring-blue-500"
                >
                  <option value="en">English</option>
                  <option value="hi">Hindi (हिंदी)</option>
                  <option value="kn">Kannada (ಕನ್ನಡ)</option>
                </select>
              </div>

              <div>
                <label className="block text-xs font-semibold uppercase tracking-wider text-gray-400 mb-2">Dynamic Session Variables</label>
                <div className="space-y-2 max-h-40 overflow-y-auto mb-3">
                  {variables.filter(v => !['appointment_date', 'appointment_time', 'doctor_specialty', 'specialty', 'appointment_id', 'booking_status', 'language', 'clinic_name'].includes(v.key)).map((v, i) => {
                    const originalIndex = variables.findIndex(item => item.key === v.key);
                    return (
                      <div key={i} className="flex justify-between items-center bg-white px-3 py-1.5 rounded-lg border border-gray-150 text-xs">
                        <span className="font-semibold text-gray-600">{v.key}:</span>
                        <input
                          type="text"
                          value={v.value}
                          onChange={(e) => {
                            const updated = [...variables];
                            if (originalIndex !== -1) {
                              updated[originalIndex].value = e.target.value;
                              setVariables(updated);
                            }
                          }}
                          className="text-right bg-transparent text-gray-800 focus:outline-none focus:border-blue-500 border-b border-transparent w-40"
                        />
                      </div>
                    );
                  })}
                </div>

                <div className="flex gap-2">
                  <input
                    type="text"
                    placeholder="Var Name"
                    value={newKey}
                    onChange={(e) => setNewKey(e.target.value)}
                    className="flex-1 px-3 py-1.5 bg-white rounded-lg border border-gray-200 text-xs text-gray-700 focus:outline-none focus:ring-1 focus:ring-blue-500"
                  />
                  <input
                    type="text"
                    placeholder="Var Value"
                    value={newValue}
                    onChange={(e) => setNewValue(e.target.value)}
                    className="flex-1 px-3 py-1.5 bg-white rounded-lg border border-gray-200 text-xs text-gray-700 focus:outline-none focus:ring-1 focus:ring-blue-500"
                  />
                  <button
                    type="button"
                    onClick={async () => {
                      if (newKey && newValue) {
                        setVariables([...variables, { key: newKey, value: newValue }]);
                        if (activeSessionId) {
                          try {
                            await fetch(API_URL + '/api/variables', {
                              method: 'POST',
                              headers: { 'Content-Type': 'application/json' },
                              body: JSON.stringify({
                                session_id: activeSessionId,
                                name: newKey,
                                value: newValue
                              })
                            });
                          } catch (e) {
                            console.error('Failed to save manual variable to DB:', e);
                          }
                        }
                        setNewKey('');
                        setNewValue('');
                      }
                    }}
                    className="px-3 py-1.5 bg-blue-600 hover:bg-blue-700 text-white font-bold rounded-lg text-xs transition-all"
                  >
                    + Add
                  </button>
                </div>
              </div>
            </div>
          </div>

          {/* Action Trigger */}
          <div className="flex flex-col items-center">
            {status === 'Listening...' || status === 'Speaking...' || status === 'Thinking...' || status === 'Connecting...' ? (
              <button
                onClick={stopCall}
                className="flex items-center space-x-2 px-6 py-4 bg-red-600 hover:bg-red-700 text-white font-bold rounded-full shadow-lg transition-all duration-300 hover:scale-105"
              >
                <PhoneOff className="h-6 w-6 animate-pulse" />
                <span>Hang Up</span>
              </button>
            ) : (
              <button
                onClick={startCall}
                className="flex items-center space-x-2 px-6 py-4 bg-blue-600 hover:bg-blue-700 text-white font-bold rounded-full shadow-lg transition-all duration-300 hover:scale-105"
              >
                <Phone className="h-6 w-6" />
                <span>Start Call</span>
              </button>
            )}
            <div className="mt-6 text-center">
              <div className="text-sm font-semibold text-gray-400 uppercase tracking-widest">Voice Status</div>
              <div className={`text-lg font-bold mt-1 ${
                status === 'Listening...' ? 'text-green-500' :
                status === 'Speaking...' ? 'text-blue-500' :
                status === 'Thinking...' ? 'text-yellow-500' :
                status === 'Appointment confirmed' ? 'text-indigo-600' : 'text-gray-500'
              }`}>{status}</div>
            </div>
          </div>
        </div>

        {/* Right Info Screen */}
        <div className="md:col-span-5 bg-gray-950 p-8 md:p-12 text-white flex flex-col justify-between">
          <div>
            <div className="flex items-center space-x-2 mb-6 text-gray-500">
              <Database className="h-4 w-4" />
              <span className="text-xs uppercase tracking-wider font-semibold">Live Appointment Extractor</span>
            </div>
            
            <h2 className="text-xl font-bold mb-8">Appointment Information</h2>

            <div className="space-y-6">
              <div className="flex items-start space-x-4">
                <User className="h-5 w-5 text-gray-600 mt-1" />
                <div>
                  <div className="text-xs uppercase font-semibold text-gray-600 tracking-wider">Doctor</div>
                  <div className="text-base font-semibold mt-0.5">{appointmentInfo.doctor}</div>
                </div>
              </div>

              <div className="flex items-start space-x-4">
                <Award className="h-5 w-5 text-gray-600 mt-1" />
                <div>
                  <div className="text-xs uppercase font-semibold text-gray-600 tracking-wider">Specialty</div>
                  <div className="text-base font-semibold mt-0.5">{appointmentInfo.specialty}</div>
                </div>
              </div>

              <div className="flex items-start space-x-4">
                <Calendar className="h-5 w-5 text-gray-600 mt-1" />
                <div>
                  <div className="text-xs uppercase font-semibold text-gray-600 tracking-wider">Date</div>
                  <div className="text-base font-semibold mt-0.5">{appointmentInfo.date}</div>
                </div>
              </div>

              <div className="flex items-start space-x-4">
                <Clock className="h-5 w-5 text-gray-600 mt-1" />
                <div>
                  <div className="text-xs uppercase font-semibold text-gray-600 tracking-wider">Time</div>
                  <div className="text-base font-semibold mt-0.5">{appointmentInfo.time}</div>
                </div>
              </div>

              <div className="flex items-start space-x-4">
                <Database className="h-5 w-5 text-gray-600 mt-1" />
                <div>
                  <div className="text-xs uppercase font-semibold text-gray-600 tracking-wider">Appointment ID</div>
                  <div className="text-base font-semibold mt-0.5">{appointmentInfo.id}</div>
                </div>
              </div>

              <div className="flex items-start space-x-4">
                <ShieldCheck className="h-5 w-5 text-gray-600 mt-1" />
                <div>
                  <div className="text-xs uppercase font-semibold text-gray-600 tracking-wider">Booking Status</div>
                  <span className={`inline-block text-xs font-semibold px-2.5 py-1 rounded-full mt-1.5 ${
                    appointmentInfo.status === 'CONFIRMED' ? 'bg-green-500/20 text-green-400' :
                    appointmentInfo.status === 'RESCHEDULED' ? 'bg-yellow-500/20 text-yellow-400' :
                    appointmentInfo.status === 'CANCELLED' ? 'bg-red-500/20 text-red-400' : 'bg-gray-800 text-gray-400'
                  }`}>{appointmentInfo.status}</span>
                </div>
              </div>
            </div>
          </div>

          <div className="mt-8 text-xs text-gray-600 text-center">
            Integrates Neon PostgreSQL & Google Calendar Sync
          </div>
        </div>

      </div>
    </div>
  );
}
