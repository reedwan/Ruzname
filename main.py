import machine

# 1. ESTABLISH POWER HOLD IMMEDIATELY (Before heavy imports)
vsys = machine.Pin(2, machine.Pin.OUT, value=1)

# --- Wake-up Time Configuration ---
WAKE_HOUR = 9      # Hour to wake up daily in UTC (0-23)
WAKE_MINUTE = 0    # Minute to wake up daily (0-59)

display = None
alarm_configured = False
battery_dead = False
rtc = None
wake_reason = "Power/USB"

def stop_processing_led():
    try:
        if 'inky_frame' in globals():
            inky_frame.led_busy.off()
    except Exception:
        pass

def show_error(message):
    global display
    print("Fault encountered: {}".format(message))
    if display is None:
        try:
            from picographics import PicoGraphics, DISPLAY_INKY_FRAME_7
            display = PicoGraphics(display=DISPLAY_INKY_FRAME_7)
        except Exception as e:
            print("Failed to initialize display for error message:", e)
            return
    try:
        display.set_pen(1)
        display.clear()
        display.set_pen(0)
        display.set_font("bitmap8")
        display.text(str(message), 600, 65, 350, 2, 90)
        display.update()
    except Exception as e:
        print("Failed to render error message to display:", e)

def atomic_write(path, value):
    temp_path = path + ".tmp"
    try:
        with open(temp_path, "w") as f:
            f.write(str(value))
            try:
                f.flush()
            except Exception:
                pass
        try:
            uos.remove(path)
        except OSError:
            pass
        uos.rename(temp_path, path)
    except Exception as e:
        print("Failed to write atomic file {}: {}".format(path, e))
        try:
            uos.remove(temp_path)
        except OSError:
            pass

try:
    import time
    import uos
    import sdcard
    import pngdec
    import secrets
    import network
    import ntptime
    import inky_frame
    from picographics import PicoGraphics, DISPLAY_INKY_FRAME_7
    from pcf85063a import PCF85063A

    # Turn on native busy LED to indicate system activity
    inky_frame.led_busy.on()

    # Deselect SD card immediately on boot to prevent bus corruption
    machine.Pin(22, machine.Pin.OUT, value=1)

    # Detect the wake trigger source for diagnostics
    try:
        if inky_frame.woken_by_rtc():
            wake_reason = "Alarm"
        elif inky_frame.woken_by_button():
            wake_reason = "Button"
    except Exception as e:
        print("Failed to detect wake source:", e)

    # 2. INITIALIZE SYSTEM COMPONENTS
    display = PicoGraphics(display=DISPLAY_INKY_FRAME_7)

    alarm_configured = False
    battery_dead = False
    measured_voltage = 0.0

    # Initialize RTC interface
    i2c = machine.I2C(0, sda=machine.Pin(4), scl=machine.Pin(5))
    rtc = PCF85063A(i2c)

    def valid_localtime(t):
        try:
            return (
                len(t) >= 8
                and 2025 <= t[0] <= 2099
                and 1 <= t[1] <= 12
                and 1 <= t[2] <= 31
                and 0 <= t[3] <= 23
                and 0 <= t[4] <= 59
                and 0 <= t[5] <= 59
            )
        except Exception:
            return False


    def check_battery_voltage():
        vsys_gate = machine.Pin(25, machine.Pin.OUT)
        vsys_gate.value(1)
        time.sleep_ms(5)
        
        vsys_adc = machine.ADC(29)
        raw = vsys_adc.read_u16()
        
        # Restore GP25 and GP29 to CYW43 wireless SPI function (alt=7)
        vsys_gate.init(machine.Pin.ALT, alt=7)
        machine.Pin(29).init(machine.Pin.ALT, alt=7)
        
        voltage = (raw * 3.3 / 65535) * 3
        return voltage


    def get_battery_state(voltage):
        if voltage >= 4.05: return "Full"
        if voltage >= 3.85: return "Good"
        if voltage >= 3.65: return "Low"
        if voltage >= 3.40: return "Critical"
        return "Empty"


    def sync_time():
        try:
            inky_frame.led_wifi.off()
        except Exception:
            pass
        try:
            wlan = network.WLAN(network.STA_IF)
            # If chip was previously active(False), active(True) re-uploads firmware cleanly
            wlan.active(True)
            
            # If already connected from a previous run (soft reboot without power cycle),
            # disconnect first so we can reconnect cleanly
            if wlan.isconnected():
                wlan.disconnect()
                time.sleep_ms(500)
            
            print("Connecting to SSID: '{}'...".format(secrets.WIFI_SSID))
            wlan.connect(secrets.WIFI_SSID, secrets.WIFI_PASSWORD)
            
            # Wait up to 45 seconds — flash native Wi-Fi LED once per second to show activity
            max_wait = 45
            led_state = True
            while max_wait > 0:
                status = wlan.status()
                if status < 0 or status >= 3:
                    break
                try:
                    if led_state:
                        inky_frame.led_wifi.on()
                    else:
                        inky_frame.led_wifi.off()
                except Exception:
                    pass
                led_state = not led_state
                max_wait -= 1
                print("waiting for connection...")
                time.sleep(1)
            
            # WiFi phase done — turn off network LED immediately
            try:
                inky_frame.led_wifi.off()
            except Exception:
                pass
                
            status = wlan.status()
            if status != 3:
                status_msgs = {
                    -3: "Bad Authentication (Wrong Password)",
                    -2: "Network Not Found (SSID Out of Range)",
                    -1: "General Connection Failure",
                    2: "No IP Address (DHCP Timeout)",
                    1: "Association Failed (In Progress)",
                    0: "WiFi Interface Down"
                }
                msg = status_msgs.get(status, "Status Code {}".format(status))
                return False, "WiFi connection failed: {}".format(msg)
                
            ntptime.settime()
            t = time.localtime(time.time())  # Pure UTC time
            
            if not valid_localtime(t):
                return False, "NTP returned an invalid time"
                
            rtc.datetime((t[0], t[1], t[2], t[3], t[4], t[5], t[6]))
            return True, None  # network_led stays off — WiFi done
        except Exception as exc:
            try:
                inky_frame.led_wifi.off()
            except Exception:
                pass
            return False, str(exc)


    def load_machine_rtc():
        try:
            t = rtc.datetime()
            machine.RTC().datetime((t[0], t[1], t[2], t[6], t[3], t[4], t[5], 0))
            now = time.localtime()
            if not valid_localtime(now):
                return False, "External RTC contains an invalid date"
            return True, None
        except Exception as exc:
            return False, str(exc)


    def update_display(diag_lines=None):
        spi = None
        sd_mounted = False
        try:
            try:
                uos.umount("/sd")
            except OSError:
                pass

            # Deassert CS and wait for card to reset its internal command buffer
            machine.Pin(22, machine.Pin.OUT, value=1)
            time.sleep_ms(10)

            spi = machine.SPI(
                0,
                baudrate=1_000_000,
                polarity=0,
                phase=0,
                sck=machine.Pin(18),
                mosi=machine.Pin(19),
                miso=machine.Pin(16)
            )

            sd = sdcard.SDCard(spi, machine.Pin(22))
            uos.mount(sd, "/sd")
            sd_mounted = True

            now = time.localtime()
            if not valid_localtime(now):
                return False, "RTC date is invalid"

            filename = "/sd/muwaqqit/{:04d}-{:02d}-{:02d}.png".format(now[0], now[1], now[2])

            try:
                uos.stat(filename)
            except OSError:
                return False, "File missing: " + filename

            display.set_pen(1)
            display.clear()

            decoder = pngdec.PNG(display)
            decoder.open_file(filename)
            decoder.decode(0, 0, mode=pngdec.PNG_COPY)

            if diag_lines:
                display.set_pen(0)
                display.set_font("bitmap8")
                display.text(diag_lines[0], 90, 62, 680, 1, 90)
                if len(diag_lines) > 1:
                    display.text(diag_lines[1], 74, 62, 680, 1, 90)

            display.update()
            return True, None
        except Exception as exc:
            return False, str(exc)
        finally:
            if sd_mounted:
                try:
                    uos.umount("/sd")
                except Exception:
                    pass
            try:
                machine.Pin(22, machine.Pin.OUT, value=1)
            except Exception:
                pass
            if spi is not None:
                try:
                    spi.deinit()
                except Exception:
                    pass



    # 2. SYNCHRONIZE AND LOAD TIME
    print("[2/4] Loading time from RTC...")
    rtc_ok, rtc_error = load_machine_rtc()
    ntp_error = None
    
    last_sync_epoch = 0
    last_attempt_epoch = 0
    if rtc_ok:
        now = time.localtime()
        print("RTC Time:", "{:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}".format(now[0], now[1], now[2], now[3], now[4], now[5]))
        try:
            with open("/last_sync_epoch.txt", "r") as f:
                last_sync_epoch = int(f.read().strip())
        except Exception:
            pass
        try:
            with open("/last_attempt_epoch.txt", "r") as f:
                last_attempt_epoch = int(f.read().strip())
        except Exception:
            pass

    now_epoch = time.time()
    one_month = 30 * 24 * 3600

    if not rtc_ok or wake_reason in ("Button", "Power/USB") or (now_epoch - last_sync_epoch >= one_month):
        atomic_write("/last_attempt_epoch.txt", now_epoch)
        last_attempt_epoch = now_epoch
            
        print("NTP sync required (monthly check or RTC lost). Connecting to Wi-Fi...")
        ntp_ok, ntp_error = sync_time()
        if ntp_ok:
            print("NTP sync successful. RTC clock updated.")
            rtc_ok, rtc_error = load_machine_rtc()
            if rtc_ok:
                now_epoch = time.time()
                atomic_write("/last_sync_epoch.txt", now_epoch)
                last_sync_epoch = now_epoch
                atomic_write("/last_attempt_epoch.txt", now_epoch)
                last_attempt_epoch = now_epoch
        else:
            print("NTP Sync failed:", ntp_error)


    # Power down WiFi chip — stops CYW43 background IRQ from interfering with SD card SPI
    try:
        network.WLAN(network.STA_IF).active(False)
    except Exception:
        pass

    if not rtc_ok:
        if ntp_error:
            raise RuntimeError("RTC unavailable (lost/reset), WiFi sync failed: {}".format(ntp_error))
        else:
            raise RuntimeError("RTC unavailable. RTC error: {}".format(rtc_error))

    # 3. CONFIGURE NEXT CYCLE (Set alarm early so that we always wake up even if rendering fails)
    print("[3/4] Setting alarm for tomorrow at {:02d}:{:02d}...".format(WAKE_HOUR, WAKE_MINUTE))
    rtc.enable_alarm_interrupt(False)
    rtc.clear_alarm_flag()
    rtc.set_alarm(0, WAKE_MINUTE, WAKE_HOUR, -1)
    rtc.enable_alarm_interrupt(True)
    print("Alarm configured successfully.")
    alarm_configured = True

    # 4. RENDER DISPLAY
    print("[4/4] Rendering calendar from SD card...")
    
    # Measure battery voltage now that Wi-Fi has been deactivated to avoid pin conflicts
    battery_measurement_ok = False
    try:
        measured_voltage = check_battery_voltage()
        battery_measurement_ok = True
        print("Battery Voltage Measured: {:.2f}V".format(measured_voltage))
    except Exception as e:
        print("Failed to read battery voltage:", e)
        
    voltage = measured_voltage

    if battery_measurement_ok:
        if voltage > 4.4:
            bat_str = "USB"
        elif voltage > 1.0:
            if voltage < 3.3:
                battery_dead = True
                raise RuntimeError("BATTERY DEAD ({:.2f}V). Please recharge.".format(voltage))
            state = get_battery_state(voltage)
            bat_str = "{:.1f}V ({})".format(voltage, state)
        else:
            bat_str = "Unknown"
    else:
        bat_str = "Unknown"
        
    diag_lines = []


    # Line 1: BAT, WAKE, RTC SYNC (and fail if failed)
    if last_sync_epoch > 0:
        t = time.localtime(last_sync_epoch)
        short_sync = "{:02d}/{:02d} {:02d}:{:02d}".format(t[2], t[1], t[3], t[4])
    else:
        short_sync = "Never"

    wake_clean = "Power" if wake_reason == "Power/USB" else wake_reason
    line1 = "BAT: {}, WAKE: {}, RTC SYNC: {}".format(bat_str, wake_clean, short_sync)
    if last_attempt_epoch > last_sync_epoch:
        tf = time.localtime(last_attempt_epoch)
        line1 += ", RTC FAIL: {:02d}/{:02d} {:02d}:{:02d}".format(tf[2], tf[1], tf[3], tf[4])
    diag_lines.append(line1)

    # Line 2: RUN, NEXT RUN
    now_time = time.localtime()
    run_str = "{:02d}/{:02d} {:02d}:{:02d}".format(now_time[2], now_time[1], now_time[3], now_time[4])
    
    # Calculate next wake run
    now_epoch = time.time()
    now_time_check = time.localtime(now_epoch)
    if now_time_check[3] < WAKE_HOUR or (now_time_check[3] == WAKE_HOUR and now_time_check[4] < WAKE_MINUTE):
        next_time = now_time_check
    else:
        next_time = time.localtime(now_epoch + 86400)
    next_str = "{:02d}/{:02d} {:02d}:{:02d}".format(next_time[2], next_time[1], WAKE_HOUR, WAKE_MINUTE)
    
    diag_lines.append("RUN: {}, NEXT RUN: {}".format(run_str, next_str))

    rendered, render_error = update_display(diag_lines=diag_lines)
    if not rendered:
        show_error("FAULT: " + render_error)

except Exception as exc:
    # 5. GLOBAL FAULT FALLBACK
    try:
        show_error("CRITICAL FAULT: " + str(exc))
    except Exception:
        pass

finally:
    # 6. ALARM ENSURANCE & UNCONDITIONAL POWER SEVERANCE
    try:
        if 'rtc' in globals() and rtc is not None:
            if battery_dead:
                print("Battery is dead. Disabling future wake alarms to prevent deep discharge.")
                rtc.enable_alarm_interrupt(False)
                rtc.clear_alarm_flag()
            elif not alarm_configured:
                rtc.enable_alarm_interrupt(False)
                rtc.clear_alarm_flag()
                rtc.set_alarm(0, WAKE_MINUTE, WAKE_HOUR, -1)
                rtc.enable_alarm_interrupt(True)
    except Exception as e:
        print("Failed to configure alarm in finally:", e)
        
    time.sleep(1)
    stop_processing_led()  # All done — processing LED off before power cut
    vsys.value(0)