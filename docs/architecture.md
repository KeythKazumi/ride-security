# Ride Security — Architecture

Diagrams describing the current state of the codebase. Mermaid syntax, renders on
GitHub and in most IDEs.

---

## 1. Component map

```mermaid
flowchart TB
    subgraph client["Client"]
        browser["Browser<br/>(templates/base.html + panels)"]
    end

    subgraph edge["Edge (optional)"]
        cf["cloudflared<br/>Cloudflare Tunnel<br/>profile: tunnel"]
    end

    subgraph web["Docker service: web — Django (config)"]
        urls["config/urls.py<br/>admin/ · accounts/ · devices/ · dashboard/"]

        subgraph accounts_app["accounts"]
            acc_views["UserLoginView<br/>UserLogoutView"]
        end

        subgraph dashboard_app["dashboard"]
            dash_views["home()<br/>aggregate counts"]
        end

        subgraph notif_app["notifications"]
            notif_views["views.py<br/>list · open · read · read_all"]
            notif_models["models.py<br/>Notification (+unread manager)"]
            notif_ctx["context_processors.py<br/>unread count for every page"]
        end

        subgraph devices_app["devices"]
            dev_views["views.py<br/>camera / nvr / sensor CRUD<br/>feed · snapshot · stream · recognize<br/>event list / detail / capture<br/>people list / detail / name"]
            dev_forms["forms.py<br/>NVRForm · CameraForm · SensorForm<br/>PersonForm · PersonNameForm"]
            dev_models["models.py<br/>NVR · Camera · Sensor · MotionEvent<br/>Person · FaceCandidate · Sighting"]
            dev_admin["admin.py<br/>Django admin (Person upload)"]
            discovery["discovery.py<br/>ONVIF WS-Discovery + TCP sweep"]
            services["services.py<br/>fetch_camera_snapshot()"]
            streaming["streaming.py<br/>generate_mjpeg_stream()<br/>get_rtsp_frame()"]
            facerec["face_recognition.py (ORM-free)<br/>search_faces() · detect_faces() (opencv→mtcnn)<br/>detect_persons() (SSD + HOG) · assess_capture()<br/>face_quality() · crop_face() · draw_recognized_faces()"]
            identities["identities.py<br/>identify_faces() · register_sightings()<br/>observe_candidate() · promote_candidate()<br/>merge_persons() · name_person()"]
            motion["motion.py<br/>iter_motion_events()<br/>MOG2 frame diff + sharpest pick"]
            events["events.py<br/>record_motion_event() · analyze_event()<br/>process_pending() · cleanup_reviewed_events()"]
            mgmt["management/commands<br/>discover_nvrs"]
        end
    end

    subgraph workers["Worker processes (compose profile: motion)"]
        watcher["manage.py watch_motion<br/>one thread per camera"]
        processor["manage.py process_events --loop<br/>deferred analysis"]
        cleaner["manage.py cleanup_events --loop<br/>purge confirmed no-face captures"]
    end

    subgraph storage["Persistence (volumes)"]
        db[("SQLite<br/>db_data → /app/data")]
        media[("MEDIA_ROOT/persons/&lt;code&gt;/<br/>reference images = DeepFace db")]
        candidates[("MEDIA_ROOT/candidates/&lt;code&gt;/<br/>faces awaiting a 2nd sighting")]
        captures[("MEDIA_ROOT/events/camera_&lt;id&gt;/<br/>captured frames")]
        weights[("deepface_weights<br/>/root/.deepface")]
    end

    subgraph libs["Third-party runtime"]
        cv2["OpenCV + FFmpeg"]
        deepface["DeepFace (SFace, opencv detector)"]
        requests["requests"]
    end

    subgraph lan["LAN"]
        nvr_dev["NVR (TP-Link VIGI)<br/>RTSP 554 · HTTP snapshot"]
        cams["Cameras (PoE / WiFi)<br/>channels 1..N"]
        sensors_dev["Sensors<br/>(records only, no polling yet)"]
    end

    browser --> cf --> urls
    browser --> urls
    urls --> acc_views
    urls --> dash_views
    urls --> dev_views
    urls --> dev_admin

    dev_views --> dev_forms --> dev_models
    dev_views --> dev_models
    dash_views --> dev_models
    dev_admin --> dev_models
    dev_models --> db
    dev_admin -- "reference_image upload" --> media

    dev_views --> discovery
    dev_views --> services
    dev_views --> streaming
    dev_views --> facerec
    dev_views --> events
    mgmt --> discovery

    urls --> notif_views
    notif_views --> notif_models --> db
    notif_ctx -- "unread count on every page" --> browser
    notif_ctx --> notif_models

    watcher --> motion
    watcher -- "record only" --> events
    processor -- "analyze pending" --> events
    motion --> cv2
    events --> facerec
    events --> identities
    events --> services
    events --> dev_models
    events -- "store frame" --> captures

    identities --> facerec
    identities --> dev_models
    identities -- "enrol / promote" --> media
    identities -- "unmatched face" --> candidates
    identities -- "new unnamed person" --> notif_models
    deepface --> candidates

    services -- "HTTP snapshot" --> requests --> nvr_dev
    services -- "fallback single frame" --> streaming
    streaming --> cv2 -- "RTSP/TCP" --> nvr_dev
    facerec --> deepface
    deepface --> media
    deepface --> weights
    facerec --> cv2

    discovery -- "UDP 3702 multicast + TCP port scan" --> nvr_dev
    nvr_dev --> cams
    sensors_dev -.-> dev_models

    %% ---- palette: one colour per component type, all with dark text ----
    classDef default fill:#ffffff,stroke:#4b5563,color:#111827;
    classDef client fill:#dbeafe,stroke:#2563eb,color:#1e3a8a;
    classDef edge fill:#ffedd5,stroke:#ea580c,color:#7c2d12,stroke-dasharray: 4 3;
    classDef routing fill:#e0e7ff,stroke:#4f46e5,color:#312e81;
    classDef view fill:#dcfce7,stroke:#16a34a,color:#14532d;
    classDef form fill:#fef3c7,stroke:#d97706,color:#78350f;
    classDef model fill:#ede9fe,stroke:#7c3aed,color:#4c1d95;
    classDef service fill:#ccfbf1,stroke:#0d9488,color:#134e4a;
    classDef store fill:#e2e8f0,stroke:#475569,color:#0f172a;
    classDef lib fill:#fce7f3,stroke:#db2777,color:#831843;
    classDef device fill:#fee2e2,stroke:#dc2626,color:#7f1d1d,stroke-dasharray: 4 3;
    classDef worker fill:#cffafe,stroke:#0891b2,color:#164e63;

    class browser client;
    class cf edge;
    class urls routing;
    class acc_views,dash_views,dev_views,dev_admin,notif_views view;
    class dev_forms form;
    class dev_models,notif_models model;
    class discovery,services,streaming,facerec,motion,events,identities,notif_ctx,mgmt service;
    class watcher,processor worker;
    class db,media,candidates,captures,weights store;
    class cv2,deepface,requests lib;
    class nvr_dev,cams,sensors_dev device;

    style client fill:#f8fafc,stroke:#94a3b8,color:#0f172a;
    style edge fill:#f8fafc,stroke:#94a3b8,color:#0f172a;
    style web fill:#f8fafc,stroke:#94a3b8,color:#0f172a;
    style accounts_app fill:#ffffff,stroke:#cbd5e1,color:#0f172a;
    style dashboard_app fill:#ffffff,stroke:#cbd5e1,color:#0f172a;
    style devices_app fill:#ffffff,stroke:#cbd5e1,color:#0f172a;
    style notif_app fill:#ffffff,stroke:#cbd5e1,color:#0f172a;
    style storage fill:#f8fafc,stroke:#94a3b8,color:#0f172a;
    style libs fill:#f8fafc,stroke:#94a3b8,color:#0f172a;
    style lan fill:#f8fafc,stroke:#94a3b8,color:#0f172a;
    style workers fill:#f8fafc,stroke:#94a3b8,color:#0f172a;
```

**Legend**

| Colour | Component type | Nodes |
| --- | --- | --- |
| Blue | Client | browser |
| Orange (dashed) | Optional edge / external service | cloudflared |
| Indigo | URL routing | `config/urls.py` |
| Green | Views / controllers | app views, Django admin, notification views |
| Amber | Forms | `devices/forms.py` |
| Purple | Models (ORM) | `devices/models.py`, `notifications/models.py` |
| Teal | Service modules | discovery, services, streaming, face_recognition, motion, events, identities, context processor, mgmt command |
| Cyan | Long-running worker processes | watch_motion, process_events, cleanup_events |
| Slate | Persistence / volumes | SQLite, person references, candidates, captures, DeepFace weights |
| Pink | Third-party runtime libs | OpenCV, DeepFace, requests |
| Red (dashed) | Physical LAN hardware | NVR, cameras, sensors |

**Notes on the current state**

- `Sensor` is persistence + UI only; nothing polls the physical sensors yet. Motion is
  detected in software from the RTSP stream, not from a `Sensor` row.
- Recognition never runs on the live stream. It runs on single frames, either on-demand
  (`/recognize/`) or against a stored `MotionEvent`.
- Capture and analysis are decoupled: `watch_motion` only writes rows, `process_events`
  analyses them. A `MotionEvent` is therefore `pending` for a while, by design.
- The DeepFace "database" is the `MEDIA_ROOT/persons/<code>/` directory tree created by
  `Person.reference_image` uploads — keyed on the immutable `code` so renaming a person
  never orphans their references.
- Storage is bounded by user confirmation: `cleanup_events` only deletes an event when
  it is `analyzed`, verdict `no_face`, *and* `reviewed_at` is set (a user agreed). Re-analysis
  clears `reviewed_at`, so a changed verdict is never auto-deleted on stale confirmation.
- The motion watcher opens its own RTSP connection per camera, independent of any browser
  viewing `/stream/`. Two viewers plus the watcher means three connections to the NVR.
- Reference directories are keyed on `Person.code`, never the name. DeepFace derives
  identity from the directory name, so a rename would otherwise orphan the references.
  `face_recognition` therefore only ever speaks in codes; `identities` maps them to rows.
- `Notification` is not per-user. This is a single-operator install, so read/unread is
  global.
- Auto-enrolment will still produce more than one identity per real person — different
  angles and lighting cross the cosine threshold. That is why naming and merging are the
  same form.
- Any structural change to a face database (promote, merge, delete) calls
  `invalidate_db_cache`, because DeepFace pickles embeddings next to the images and would
  otherwise keep matching identities that have moved.

---

## 2. Sequence diagrams

### 2.1 Live MJPEG stream

```mermaid
sequenceDiagram
    autonumber
    actor U as User (browser)
    participant V as devices.views.camera_stream
    participant M as Camera (model)
    participant S as streaming.generate_mjpeg_stream
    participant CV as OpenCV / FFmpeg
    participant N as NVR (LAN)

    U->>V: GET /devices/cameras/{id}/stream/
    Note over V: @login_required
    V->>M: get_object_or_404(Camera, pk=id)
    V->>M: get_rtsp_url()
    M-->>V: rtsp://user:pass@ip:554/live/{channel}/1/avm
    alt no RTSP URL
        V-->>U: 404 "No RTSP URL configured"
    else URL present
        V-->>U: StreamingHttpResponse(multipart/x-mixed-replace)
        V->>S: generate_mjpeg_stream(camera, fps=20)
        S->>CV: VideoCapture(url, CAP_FFMPEG)<br/>rtsp_transport=tcp, timeout=5s
        CV->>N: RTSP DESCRIBE / SETUP / PLAY (TCP)
        N-->>CV: RTP media
        loop until client disconnects
            S->>CV: cap.read()
            alt frame ok
                CV-->>S: frame
                S->>CV: imencode(".jpg")
                S-->>U: --frame + JPEG bytes
                S->>S: sleep(1/fps)
            else read failed
                S->>CV: release() + reopen (max 3 retries)
                Note over S: give up and end stream after 3
            end
        end
        S->>CV: cap.release() (finally)
    end
```

### 2.2 Snapshot with fallback

```mermaid
sequenceDiagram
    autonumber
    actor U as User (browser)
    participant V as views.camera_snapshot
    participant SV as services.fetch_camera_snapshot
    participant R as requests
    participant ST as streaming.get_rtsp_frame
    participant N as NVR (LAN)

    U->>V: GET /devices/cameras/{id}/snapshot/
    V->>SV: fetch_camera_snapshot(camera)
    SV->>SV: camera.get_snapshot_url()
    SV->>R: GET {proto}://ip:{snapshot_port}/cgi-bin/snapshot.cgi?channel=..
    R->>N: HTTP(S) GET (basic auth)
    alt 2xx and content is an image
        N-->>R: JPEG
        R-->>SV: bytes
    else request error / non-image content-type
        Note over SV: log warning, fall through
        SV->>ST: get_rtsp_frame(camera, timeout=10)
        ST->>N: RTSP over TCP, poll up to 10s
        alt frame captured
            ST-->>SV: JPEG bytes
        else
            ST-->>SV: None
        end
    end
    SV-->>V: bytes or None
    alt bytes
        V-->>U: 200 image/jpeg
    else None
        V-->>U: 502 "Snapshot unavailable"
    end
```

### 2.3 Face recognition on a snapshot

```mermaid
sequenceDiagram
    autonumber
    actor U as User (browser)
    participant V as views.camera_recognize
    participant SV as services.fetch_camera_snapshot
    participant TMP as tempfile (.jpg)
    participant FR as face_recognition
    participant DF as DeepFace
    participant FS as MEDIA_ROOT/persons/

    U->>V: GET /devices/cameras/{id}/recognize/
    V->>SV: fetch_camera_snapshot(camera)
    SV-->>V: image_bytes | None
    alt no image
        V-->>U: 502 "Snapshot unavailable"
    else image
        V->>TMP: write bytes, delete=False
        V->>FR: recognize_people(tmp_path)
        FR->>FS: mkdir -p persons/ then check if empty
        alt no reference persons
            FR-->>V: []
        else
            FR->>DF: find(img_path, db_path=persons/,<br/>SFace, opencv, cosine, enforce_detection=False)
            DF->>FS: load/refresh embeddings per Person folder
            DF-->>FR: DataFrame(s) per detected face
            loop each non-empty DataFrame
                FR->>FR: name = Path(identity).parent.name
                FR->>FR: distance > threshold ⇒ "Unknown"
                FR->>FR: box = (source_x, source_y, source_w, source_h)
            end
            FR-->>V: [{name, distance, box}, ...]
        end
        V->>FR: draw_recognized_faces(tmp_path, faces)
        FR->>FR: cv2 rectangle+putText<br/>green=known, red=Unknown
        FR-->>V: annotated JPEG bytes | None
        V->>TMP: os.remove (finally)
        alt annotated
            V-->>U: 200 image/jpeg
        else
            V-->>U: 502 "Could not annotate image"
        end
    end
```

### 2.4 Motion → capture → deferred analysis

```mermaid
sequenceDiagram
    autonumber
    participant W as manage.py watch_motion<br/>(thread per camera)
    participant M as motion.iter_motion_events
    participant CV as OpenCV / MOG2
    participant N as NVR (LAN)
    participant E as events.record_motion_event
    participant DB as MotionEvent table
    participant FS as MEDIA_ROOT/events/
    participant P as manage.py process_events
    participant FR as face_recognition
    actor U as User (browser)

    W->>M: iter_motion_events(camera, min_area, cooldown, sample_fps)
    M->>CV: VideoCapture(rtsp_url) + MOG2 subtractor
    CV->>N: RTSP over TCP

    loop every sampled frame
        M->>CV: cap.read() then subtractor.apply()
        CV-->>M: foreground mask
        M->>M: score = foreground px / total px
        alt score < min_area, or warming up, or cooling down
            Note over M: ignore
        else motion
            M->>CV: read burst of 5 frames
            M->>M: keep the sharpest (Laplacian variance)
            M-->>W: yield (score, jpeg_bytes)
            W->>E: record_motion_event(camera, frame_diff, jpeg, score)
            E->>FS: write events/camera_N/YYYYmmdd-HHMMSS-ffffff.jpg
            E->>DB: INSERT status=pending
            Note over M: cooldown armed for 15s
        end
    end

    Note over P: separate process, own cadence
    P->>DB: SELECT WHERE status=pending ORDER BY detected_at
    loop each pending event
        P->>FR: assess_capture(tmp path)
        FR->>FR: DeepFace.extract_faces (opencv)<br/>discard whole-frame fallback box
        FR->>FR: largest face, min(w,h) vs 80px,<br/>Laplacian variance vs 40
        FR-->>P: face_count, best_box, sharpness, verdict
        opt faces found AND a Person exists
            P->>FR: recognize_people(tmp path)
            FR-->>P: names + distances
        end
        P->>DB: UPDATE quality, metrics, faces, recognized, status=analyzed
    end

    U->>DB: GET /devices/events/
    DB-->>U: thumbnails + verdict per capture
```

### 2.5 Building the face database (auto-enrolment)

Runs inside `analyze_event`, once per detected face.

```mermaid
sequenceDiagram
    autonumber
    participant E as events.analyze_event
    participant I as identities
    participant FR as face_recognition
    participant PDB as persons/CODE/
    participant CDB as candidates/CODE/
    participant DB as Person / FaceCandidate / Sighting
    participant N as Notification
    actor U as User

    E->>I: identify_faces(image, detected boxes)
    alt at least one Person enrolled
        I->>FR: search_faces(image, persons)
        FR-->>I: per face: code + distance vs threshold
    else database empty
        Note over I: every face is unmatched
    end

    E->>I: register_sightings(event, matches, image)
    I->>DB: delete existing sightings (re-analysis is idempotent)

    loop each face
        alt matched a known code
            I->>DB: Sighting(event, person, distance)
            I->>DB: person.last_seen_at = event.detected_at
        else unmatched
            I->>FR: face_quality(image, box)
            alt face under 80px or blurrier than 40
                Note over I: dropped — too weak to enrol
            else good enough
                I->>FR: crop_face(image, box, margin 0.3)
                I->>FR: search_faces(crop, candidates)
                alt no candidate matches
                    I->>CDB: write crop
                    I->>DB: FaceCandidate(sighting_count=1)
                    Note over I: no Person yet — one sighting is not evidence
                else candidate matches
                    I->>DB: candidate.sighting_count += 1
                    alt still below 2 sightings
                        Note over I: keep waiting
                    else 2nd sighting reached
                        I->>PDB: move crop into a new persons/CODE/
                        I->>DB: Person(auto_created=True, name="")
                        I->>DB: delete candidate + its directory
                        I->>FR: invalidate_db_cache(persons, candidates)
                        I->>DB: Sighting(is_enrollment=True)
                        I->>N: "New face detected" linking to the naming form
                    end
                end
            end
        end
    end

    I-->>E: sightings, promoted people
    E->>DB: save faces JSON + recognized display names

    U->>N: sees unread badge, opens notification
    U->>DB: names the person, or merges into an existing one
    Note over DB: naming marks the notification read<br/>merging moves references and repoints sightings
```

### 2.6 LAN discovery → add NVR

```mermaid
sequenceDiagram
    autonumber
    actor U as User (browser)
    participant V as views.nvr_discover
    participant D as discovery
    participant NET as LAN
    participant DB as NVR table
    participant C as views.nvr_create

    U->>V: GET /devices/nvrs/discover/?scan=1&network=192.168.68.0/24
    V->>D: discover_devices(network)
    D->>NET: UDP multicast 239.255.255.250:3702 (ONVIF Probe)
    NET-->>D: SOAP responses → service_urls per IP
    opt include_scan
        D->>D: get_local_networks() if no CIDR given
        D->>NET: TCP connect sweep, 128 workers<br/>ports 80,443,554,2020,8000,8080,8443,8899,20443,37777
        NET-->>D: open ports per IP
    end
    D->>D: merge onvif + scan, suggest_ports() → field names
    D-->>V: [{ip_address, source, open_ports, service_urls, suggested}]
    V->>DB: values_list("ip_address") → mark already_added
    V-->>U: nvr_discover.html (candidate table)

    U->>C: GET /devices/nvrs/add/?ip_address=..&rtsp_port=..&...
    C->>C: NVRForm(initial=prefill from query params)
    C-->>U: nvr_form.html
    U->>C: POST credentials + ports
    C->>DB: form.save()
    C-->>U: 302 → devices:nvrs
```

---

## 3. Class diagram

```mermaid
classDiagram
    direction LR

    class NVR {
        +CharField name
        +GenericIPAddressField ip_address
        +PositiveIntegerField channel_count = 4 (4/8/10/16)
        +PositiveIntegerField port = 554
        +PositiveIntegerField https_port?
        +PositiveIntegerField service_port?
        +PositiveIntegerField management_port?
        +PositiveIntegerField remote_stream_port?
        +PositiveIntegerField rtsp_port?
        +PositiveIntegerField openapi_port?
        +BooleanField use_https = False
        +CharField username
        +CharField password
        +CharField location
        +BooleanField is_online = False
        +DateTimeField created_at
        +DateTimeField updated_at
        +get_rtsp_port() int
        +get_snapshot_port() int
        +get_snapshot_protocol() str
        +rtsp_base_url() str
        +__str__() str
    }

    class Camera {
        +CharField name
        +FK nvr? (SET_NULL, related_name=cameras)
        +PositiveIntegerField channel = 1
        +CharField location
        +CharField rtsp_url (override)
        +CharField snapshot_url (override)
        +CharField status = OFFLINE
        +DateTimeField created_at
        +DateTimeField updated_at
        +get_rtsp_url() str
        +get_snapshot_url() str
        +__str__() str
    }

    class CameraStatus {
        <<TextChoices>>
        ONLINE
        OFFLINE
        RECORDING
    }

    class Sensor {
        +CharField name
        +CharField sensor_type = MOTION
        +CharField location
        +GenericIPAddressField ip_address?
        +CharField status = NORMAL
        +DateTimeField created_at
        +DateTimeField updated_at
        +__str__() str
    }

    class SensorType {
        <<TextChoices>>
        MOTION
        DOOR
        SMOKE
        GLASS
        OTHER
    }

    class SensorStatus {
        <<TextChoices>>
        NORMAL
        TRIGGERED
        OFFLINE
        LOW_BATTERY
    }

    class Person {
        +CharField code (unique, immutable)
        +CharField name (blank until labelled)
        +ImageField reference_image
        +BooleanField auto_created = False
        +DateTimeField last_seen_at?
        +DateTimeField created_at
        +DateTimeField updated_at
        +is_named bool
        +display_name str
        +__str__() str
    }

    class FaceCandidate {
        <<holding area>>
        PROMOTE_AFTER_SIGHTINGS = 2
        +CharField code (unique)
        +ImageField image
        +FK camera? (SET_NULL)
        +PositiveIntegerField sighting_count = 1
        +DateTimeField first_seen_at
        +DateTimeField last_seen_at
        +ready_to_promote bool
        +__str__() str
    }

    class Sighting {
        +FK event (CASCADE, related_name=sightings)
        +FK person (CASCADE, related_name=sightings)
        +FloatField distance?
        +JSONField box
        +BooleanField is_enrollment = False
        +DateTimeField created_at
        +__str__() str
    }

    class Notification {
        <<notifications app>>
        +CharField kind = INFO
        +CharField title
        +TextField message
        +CharField url
        +BooleanField is_read = False
        +DateTimeField read_at?
        +DateTimeField created_at
        +mark_read() Notification
        +mark_unread() Notification
    }

    class NotificationKind {
        <<TextChoices>>
        NEW_PERSON
        INFO
        WARNING
    }

    class MotionEvent {
        +FK camera (CASCADE, related_name=motion_events)
        +CharField source = FRAME_DIFF
        +ImageField image
        +DateTimeField detected_at
        +FloatField motion_score?
        +CharField status = PENDING
        +DateTimeField analyzed_at?
        +TextField error
        +PositiveIntegerField face_count = 0
        +PositiveIntegerField best_face_width = 0
        +PositiveIntegerField best_face_height = 0
        +FloatField blur_score?
        +CharField quality = UNKNOWN
        +JSONField faces = list
        +JSONField recognized = list
        +JSONField persons = list
        +DateTimeField reviewed_at?
        +is_usable bool
        +is_reviewed bool
        +best_face_size str?
        +recognized_display str
        +__str__() str
    }

    class EventSource {
        <<TextChoices>>
        FRAME_DIFF
        MANUAL
        ONVIF
        NVR_PUSH
    }

    class EventStatus {
        <<TextChoices>>
        PENDING
        ANALYZED
        FAILED
    }

    class EventQuality {
        <<TextChoices>>
        UNKNOWN
        USABLE
        PERSON
        NO_FACE
        TOO_SMALL
        TOO_BLURRY
    }

    class Model {
        <<django.db.models>>
    }

    Model <|-- NVR
    Model <|-- Camera
    Model <|-- Sensor
    Model <|-- Person
    Model <|-- MotionEvent
    Model <|-- FaceCandidate
    Model <|-- Sighting
    Model <|-- Notification

    NVR "0..1" o-- "0..*" Camera : cameras
    Camera "1" *-- "0..*" MotionEvent : motion_events
    Camera "0..1" o-- "0..*" FaceCandidate : face_candidates
    MotionEvent "1" *-- "0..*" Sighting : sightings
    Person "1" *-- "0..*" Sighting : sightings
    FaceCandidate ..> Person : promoted after 2 sightings
    Person ..> Notification : raises NEW_PERSON when auto-created
    Notification ..> NotificationKind
    Camera ..> CameraStatus
    Sensor ..> SensorType
    Sensor ..> SensorStatus
    MotionEvent ..> EventSource
    MotionEvent ..> EventStatus
    MotionEvent ..> EventQuality
    MotionEvent ..> MediaStorage : _event_upload_to
    Person ..> MediaStorage : _person_upload_to

    class MediaStorage {
        <<filesystem>>
        MEDIA_ROOT/persons/&lt;name&gt;/&lt;file&gt;
        doubles as the DeepFace db_path
    }
```

### Forms and views

```mermaid
classDiagram
    direction TB

    class ModelForm {
        <<django.forms>>
    }
    class NVRForm {
        Meta.model = NVR
        15 fields incl. all ports
        password → PasswordInput(render_value)
    }
    class CameraForm {
        Meta.model = Camera
        channel: TypedChoiceField(int)
        __init__() limits choices to nvr.channel_count (default 16)
    }
    class SensorForm {
        Meta.model = Sensor
        name, sensor_type, location, ip_address, status
    }
    ModelForm <|-- NVRForm
    ModelForm <|-- CameraForm
    ModelForm <|-- SensorForm

    class LoginView {
        <<django.contrib.auth.views>>
    }
    class LogoutView {
        <<django.contrib.auth.views>>
    }
    class UserLoginView {
        template_name = accounts/login.html
        redirect_authenticated_user = True
    }
    class UserLogoutView {
        next_page = accounts:login
    }
    LoginView <|-- UserLoginView
    LogoutView <|-- UserLogoutView

    class ModelAdmin {
        <<django.contrib.admin>>
    }
    class NVRAdmin {
        fieldsets: general + Ports
        get_snapshot_port() / get_rtsp_port() display
    }
    class CameraAdmin {
        fieldsets: general + Connection
    }
    class PersonAdmin
    class SensorAdmin
    ModelAdmin <|-- NVRAdmin
    ModelAdmin <|-- CameraAdmin
    ModelAdmin <|-- PersonAdmin
    ModelAdmin <|-- SensorAdmin
```

### Service modules (function-level, no classes)

```mermaid
classDiagram
    direction LR

    class streaming {
        <<module>>
        FFMPEG_CAPTURE_OPTIONS : rtsp_transport tcp, timeout 5s
        -_open_capture(rtsp_url) VideoCapture
        +generate_mjpeg_stream(camera, fps=20) Iterator~bytes~
        +get_rtsp_frame(camera, timeout=10) bytes?
    }

    class services {
        <<module>>
        +fetch_camera_snapshot(camera) bytes?
    }

    class face_recognition {
        <<module, no ORM>>
        MIN_FACE_PX = 80
        MIN_FACE_SHARPNESS = 40.0
        MIN_DETECTION_CONFIDENCE = 0.01
        PERSON_DB = persons
        CANDIDATE_DB = candidates
        -_load_image(path_or_bytes) ndarray
        -_sharpness(image) float
        +db_path(name, ensure) Path
        +invalidate_db_cache(name)
        +search_faces(image_path, db_name) list~Match~
        +detect_faces(image_path) list~Face~
        +assess_capture(image_path) Assessment
        +face_quality(image_path, box) dict
        +crop_face(image_path, box, margin) bytes?
        +draw_recognized_faces(image_path, faces) bytes?
    }

    class identities {
        <<module, ORM + DeepFace>>
        +label_faces(faces) list
        +identify_faces(image_path, boxes) list~Match~
        +register_sightings(event, matches, image_path) tuple
        +observe_candidate(image_path, box, event) Person?
        +promote_candidate(candidate, crop) Person
        +merge_persons(source, target) Person
        +name_person(person, name) Person
        +delete_person(person)
    }

    class Match {
        <<dict>>
        code : str or None
        matched : bool
        distance : float
        threshold : float
        box : x, y, w, h
    }

    class motion {
        <<module>>
        DEFAULT_MIN_AREA_RATIO = 0.005
        DEFAULT_COOLDOWN = 15.0
        DEFAULT_WARMUP_FRAMES = 30
        DEFAULT_SAMPLE_FPS = 5
        DEFAULT_BURST_FRAMES = 5
        -_background_subtractor() MOG2
        -_foreground_ratio(subtractor, frame) float
        +sharpness(frame) float
        +iter_motion_events(camera, ...) Iterator
    }

    class events {
        <<module>>
        -_as_temp_file(image_bytes) contextmanager
        -_event_bytes(event) bytes
        +record_motion_event(camera, source, image_bytes, motion_score) MotionEvent?
        +analyze_event(event) MotionEvent
        +annotate_event(event) bytes?
        +process_pending(limit) list~MotionEvent~
    }

    class Assessment {
        <<dict>>
        face_count : int
        best_box : x, y, w, h
        best_sharpness : float
        quality : no_face, too_small, too_blurry, usable
        faces : list
    }

    class discovery {
        <<module>>
        WS_DISCOVERY_ADDRESS = 239.255.255.250:3702
        CANDIDATE_PORTS : dict~int,role~
        +get_local_networks() list~IPv4Network~
        +probe_onvif(timeout=4) dict
        -_check_port(ip, port, timeout) bool
        +scan_subnet(network, ports, timeout, max_workers=128) dict
        +discover_devices(network, ...) list~dict~
        +suggest_ports(open_ports) dict
    }

    class Face {
        <<dict>>
        name : str or Unknown
        distance : float
        box : x, y, w, h
    }

    services ..> streaming : RTSP fallback
    face_recognition ..> Face : returns
    face_recognition ..> Assessment : returns
    face_recognition ..> Match : returns
    motion ..> streaming : reuses _open_capture
    events ..> face_recognition : detect + score
    events ..> identities : identify + enrol
    events ..> services : snapshot when no frame supplied
    identities ..> face_recognition : codes in, never names
    discovery ..> discovery : suggest_ports maps to NVR fields
```

---

## 4. URL surface

| Path | View | Purpose |
| --- | --- | --- |
| `/` | RedirectView | → `dashboard:home` |
| `/accounts/…` | `UserLoginView` / `UserLogoutView` | auth |
| `/dashboard/` | `dashboard.home` | counts + first 6 cameras/sensors, 4 NVRs |
| `/devices/cameras/` | `camera_list` | list |
| `/devices/cameras/add/`, `/{id}/edit/`, `/{id}/delete/` | `camera_*` | CRUD |
| `/devices/cameras/{id}/feed/` | `camera_feed` | page embedding the stream |
| `/devices/cameras/{id}/snapshot/` | `camera_snapshot` | JPEG proxy |
| `/devices/cameras/{id}/stream/` | `camera_stream` | MJPEG |
| `/devices/cameras/{id}/recognize/` | `camera_recognize` | annotated JPEG |
| `/devices/cameras/{id}/capture/` | `event_capture` | POST: capture + analyze now |
| `/devices/events/` | `event_list` | captures + quality verdicts |
| `/devices/events/{id}/` | `event_detail` | metrics, frame, annotated frame |
| `/devices/events/{id}/image/` | `event_image` | stored frame |
| `/devices/events/{id}/annotated/` | `event_annotated` | frame with face boxes |
| `/devices/events/{id}/analyze/` | `event_analyze` | POST: analyze now |
| `/devices/events/{id}/review/` | `event_review` | POST: toggle user confirmation |
| `/devices/events/{id}/delete/` | `event_delete` | remove capture + stored frame |
| `/devices/events/review-no-face/` | `event_review_no_face` | POST: confirm every no-face verdict |
| `/devices/events/cleanup/` | `event_cleanup_now` | POST: run the purge immediately |
| `/devices/people/` | `person_list` | face database: unnamed, named, candidates |
| `/devices/people/add/` | `person_create` | manual enrolment |
| `/devices/people/{id}/` | `person_detail` | reference + every sighting |
| `/devices/people/{id}/image/` | `person_image` | reference image |
| `/devices/people/{id}/name/` | `person_name` | name, or merge into another person |
| `/devices/people/{id}/delete/` | `person_delete` | remove identity + directory |
| `/devices/candidates/{id}/image/` | `candidate_image` | pending candidate crop |
| `/devices/candidates/{id}/delete/` | `candidate_delete` | remove candidate + directory |
| `/notifications/` | `notification_list` | unread by default, `?show=all` for everything |
| `/notifications/{id}/open/` | `notification_open` | mark read then follow the link |
| `/notifications/{id}/read/` | `notification_mark_read` | POST |
| `/notifications/read-all/` | `notification_mark_all_read` | POST |
| `/devices/sensors/…` | `sensor_*` | CRUD |
| `/devices/nvrs/…` | `nvr_*` | CRUD |
| `/devices/nvrs/discover/` | `nvr_discover` | LAN scan |
| `/admin/` | Django admin | includes `Person` reference images and `MotionEvent` rows |

All device/dashboard views are `@login_required`.

---

## 5. Background commands

| Command | Purpose |
| --- | --- |
| `manage.py discover_nvrs [--network CIDR]` | one-shot LAN scan |
| `manage.py watch_motion [--camera ID] [--min-area] [--cooldown] [--sample-fps] [--analyze]` | long-running motion watcher, one thread per camera |
| `manage.py process_events [--limit N] [--loop] [--interval S]` | analyse pending captures |
| `manage.py cleanup_events [--loop] [--interval S] [--dry-run]` | delete confirmed no-face captures (default interval: 6h) |

`watch_motion`, `process_events` and `cleanup_events` also exist as compose services
behind the `motion` profile: `docker compose --profile motion up -d`.
