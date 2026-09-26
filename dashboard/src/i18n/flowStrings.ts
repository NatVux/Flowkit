// Strings for the "Tạo video mới" flow (status bar, new video, story, run, my videos).
// Merged into translations.ts. flowVi must translate every key (enforced by its type).

export const flowEn = {
  // ---- navigation ----
  'nav.newVideo': 'New video',
  'nav.myVideos': 'My videos',
  'nav.advanced': 'Advanced',
  'app.breadcrumb.newVideo': 'new video',
  'app.breadcrumb.story': 'story',
  'app.breadcrumb.run': 'progress',
  'app.breadcrumb.myVideos': 'my videos',

  // ---- status bar ----
  'status.ready': 'Ready',
  'status.checking': 'Checking…',
  'status.server': 'Server',
  'status.extension': 'Chrome extension',
  'status.flowTab': 'Flow tab',
  'status.cooldown': 'Google cooldown',
  'status.gemini': 'Gemini',
  'status.fix.server': 'The server is not running. Double-click start.bat in the flowkit folder.',
  'status.fix.extension': 'The Flow Kit Chrome extension is not connected. Open Chrome and check that the Flow Kit extension is on.',
  'status.fix.flowTab': 'Open a flow.google.com tab and sign in, then keep it open.',
  'status.fix.cooldown': 'Google asked to slow down. Wait {time} before creating anything.',
  'status.fix.gemini': 'Gemini is not set up: set the GEMINI_API_KEY environment variable in Windows, then restart the server.',
  'status.problems': '{n} thing(s) need attention',
} as const

export type FlowKey = keyof typeof flowEn

export const flowVi: Record<FlowKey, string> = {
  'nav.newVideo': 'Tạo video mới',
  'nav.myVideos': 'Video của tôi',
  'nav.advanced': 'Nâng cao',
  'app.breadcrumb.newVideo': 'tạo video mới',
  'app.breadcrumb.story': 'câu chuyện',
  'app.breadcrumb.run': 'tiến độ',
  'app.breadcrumb.myVideos': 'video của tôi',

  'status.ready': 'Sẵn sàng',
  'status.checking': 'Đang kiểm tra…',
  'status.server': 'Máy chủ',
  'status.extension': 'Tiện ích Chrome',
  'status.flowTab': 'Tab Flow',
  'status.cooldown': 'Google tạm nghỉ',
  'status.gemini': 'Gemini',
  'status.fix.server': 'Máy chủ chưa chạy. Hãy bấm đúp start.bat trong thư mục flowkit.',
  'status.fix.extension': 'Tiện ích Chrome Flow Kit chưa kết nối. Hãy mở Chrome và kiểm tra tiện ích Flow Kit đang bật.',
  'status.fix.flowTab': 'Hãy mở tab flow.google.com, đăng nhập và để tab đó mở.',
  'status.fix.cooldown': 'Google yêu cầu tạm nghỉ. Chờ thêm {time} rồi hãy tạo tiếp.',
  'status.fix.gemini': 'Gemini chưa được cấu hình: đặt biến môi trường GEMINI_API_KEY trong Windows rồi khởi động lại máy chủ.',
  'status.problems': 'Có {n} việc cần xử lý',
}
