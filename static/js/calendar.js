/* Calendar Views — Month / Week / Day / List */
(function() {
    'use strict';

    var D = window.CAL_DATA;
    if (!D) return;

    // Category colors for activity bubbles
    var CAT_COLORS = {
        temple:    { bg: '#7c2d12', border: '#ea580c' },
        food:      { bg: '#78350f', border: '#f59e0b' },
        culture:   { bg: '#1e3a5f', border: '#60a5fa' },
        nature:    { bg: '#14532d', border: '#4ade80' },
        nightlife: { bg: '#581c87', border: '#c084fc' },
        shopping:  { bg: '#831843', border: '#f472b6' },
        transit:   { bg: '#374151', border: '#9ca3af' },
        logistics: { bg: '#374151', border: '#9ca3af' },
    };

    var TIME_SLOTS = ['morning', 'afternoon', 'evening', 'night'];
    var SLOT_LABELS = { morning: 'Morning', afternoon: 'Afternoon', evening: 'Evening', night: 'Night' };

    // --- View Switcher ---
    var switcher = document.getElementById('calViewSwitcher');
    var views = {
        month: document.getElementById('calMonth'),
        week:  document.getElementById('calWeek'),
        day:   document.getElementById('calDay'),
        list:  document.getElementById('calList')
    };
    var currentView = localStorage.getItem('cal_view') || 'month';
    // Auto-switch to day view during trip
    if (D.tripStarted && !localStorage.getItem('cal_view')) {
        currentView = 'day';
    }

    function switchView(view) {
        currentView = view;
        localStorage.setItem('cal_view', view);
        Object.keys(views).forEach(function(k) {
            views[k].style.display = k === view ? '' : 'none';
        });
        var btns = switcher.querySelectorAll('.cal-view-btn');
        for (var i = 0; i < btns.length; i++) {
            btns[i].classList.toggle('active', btns[i].dataset.view === view);
        }
        // Scroll to today in list view
        if (view === 'list') {
            var el = document.getElementById('calToday');
            if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
    }

    switcher.addEventListener('click', function(e) {
        var btn = e.target.closest('.cal-view-btn');
        if (btn) switchView(btn.dataset.view);
    });

    // --- MONTH VIEW ---
    function buildMonthGrid() {
        var grid = document.querySelector('.cal-month-grid');
        // April 2026 starts on Wednesday (day 3, 0=Sun)
        // We already have 7 header cells
        var startDow = 3; // Wednesday

        // Build a lookup: day-of-month → trip day data
        var dayMap = {};
        D.monthData.forEach(function(d) { dayMap[d.date_day] = d; });

        // Empty cells before Apr 1
        for (var i = 0; i < startDow; i++) {
            var empty = document.createElement('div');
            empty.className = 'cal-grid-cell empty';
            grid.appendChild(empty);
        }

        // 30 days in April
        for (var day = 1; day <= 30; day++) {
            var cell = document.createElement('div');
            var tripDay = dayMap[day];
            var isTrip = !!tripDay;
            cell.className = 'cal-grid-cell' + (isTrip ? ' trip-day' : ' non-trip');
            if (D.todayDayNum && tripDay && tripDay.day_number === D.todayDayNum) {
                cell.className += ' today';
            }
            cell.dataset.day = day;

            var html = '<span class="mg-date">' + day + '</span>';
            if (isTrip) {
                html += '<span class="mg-trip-num">D' + tripDay.day_number + '</span>';
                html += '<span class="mg-icon">' + tripDay.type_emoji + '</span>';
                if (tripDay.activity_count > 0) {
                    html += '<span class="mg-count">' + tripDay.activity_count + '</span>';
                }
                cell.dataset.dayNumber = tripDay.day_number;
            }
            cell.innerHTML = html;

            if (isTrip) {
                cell.addEventListener('click', function() {
                    window.location.href = '/day/' + this.dataset.dayNumber;
                });
                cell.style.cursor = 'pointer';
            }

            grid.appendChild(cell);
        }

        // Fill remaining cells to complete last row
        var totalCells = startDow + 30;
        var remainder = totalCells % 7;
        if (remainder > 0) {
            for (var j = 0; j < 7 - remainder; j++) {
                var emp = document.createElement('div');
                emp.className = 'cal-grid-cell empty';
                grid.appendChild(emp);
            }
        }

        // Build accommodation span bars
        buildAccomBars(grid, startDow);

        // Build legend
        buildAccomLegend();
    }

    function buildAccomBars(grid, startDow) {
        // Create an overlay container for accommodation bars
        var overlay = document.createElement('div');
        overlay.className = 'cal-accom-overlay';
        grid.style.position = 'relative';
        grid.appendChild(overlay);

        D.accomSpans.forEach(function(span) {
            var cinDay = parseInt(span.check_in.split('-')[2]);
            var coutDay = parseInt(span.check_out.split('-')[2]);
            var nights = coutDay - cinDay;
            if (nights <= 0) return;

            // Calculate grid positions (0-indexed from Apr 1)
            var startIdx = (cinDay - 1) + startDow; // grid cell index
            var startRow = Math.floor(startIdx / 7);
            var startCol = startIdx % 7;

            // The bar might wrap across rows
            var remaining = nights;
            var currentCol = startCol;
            var currentRow = startRow;

            while (remaining > 0) {
                var colsInThisRow = Math.min(remaining, 7 - currentCol);
                var bar = document.createElement('div');
                bar.className = 'cal-accom-bar';
                bar.style.cssText =
                    'grid-column: ' + (currentCol + 1) + ' / span ' + colsInThisRow + ';' +
                    'grid-row: ' + (currentRow + 2) + ';' + // +2 for header row + 1-indexed
                    'background: ' + span.color_bg + ';' +
                    'box-shadow: 0 0 8px ' + span.color_glow + ';';
                // Only show name on first segment
                if (currentCol === startCol && currentRow === startRow) {
                    bar.textContent = span.name;
                    bar.title = span.name + ' (' + span.num_nights + 'n)';
                }
                bar.dataset.locId = span.location_id;
                bar.addEventListener('click', function(e) {
                    e.stopPropagation();
                    window.location.href = '/accommodations#loc-' + this.dataset.locId;
                });
                grid.appendChild(bar);

                remaining -= colsInThisRow;
                currentCol = 0;
                currentRow++;
            }
        });
    }

    function buildAccomLegend() {
        var legend = document.getElementById('calAccomLegend');
        D.accomSpans.forEach(function(span) {
            var item = document.createElement('div');
            item.className = 'cal-legend-item';
            item.innerHTML = '<span class="cal-legend-dot" style="background:' + span.color_bg +
                ';box-shadow:0 0 6px ' + span.color_glow + '"></span>' +
                '<span class="cal-legend-name">' + span.city + ': ' + span.name +
                ' <span class="cal-legend-nights">(' + span.num_nights + 'n)</span></span>';
            legend.appendChild(item);
        });
    }

    // --- WEEK VIEW ---
    var currentWeek = 1;

    function buildWeekView(week) {
        currentWeek = week;
        var container = document.getElementById('calWeekContainer');
        container.innerHTML = '';

        // Week 1: days 1-7, Week 2: days 8-14
        var startDay = (week - 1) * 7 + 1;
        var endDay = startDay + 6;

        for (var dn = startDay; dn <= endDay; dn++) {
            var wd = D.weekData[dn];
            if (!wd) continue;

            var dayEl = document.createElement('div');
            dayEl.className = 'cal-week-day';
            if (D.todayDayNum === dn) dayEl.classList.add('today');

            // Day header
            var dateObj = new Date(wd.date + 'T12:00:00');
            var weekday = dateObj.toLocaleDateString('en-US', { weekday: 'short' });
            var dayNum = dateObj.getDate();

            var header = '<div class="cw-day-header">' +
                '<a href="/day/' + dn + '" class="cw-day-link">' +
                '<span class="cw-weekday">' + weekday + '</span>' +
                '<span class="cw-datenum">' + dayNum + '</span>' +
                '<span class="cw-emoji">' + wd.type_emoji + '</span>' +
                '<span class="cw-title">Day ' + dn + ': ' + wd.title + '</span>' +
                '</a>';

            // Accommodation badge
            if (wd.accom_name) {
                header += '<div class="cw-accom">';
                if (wd.accom_check_in) header += '<span class="cw-accom-tag">IN</span> ';
                header += wd.accom_name;
                if (wd.accom_check_out && !wd.accom_check_in) header += ' <span class="cw-accom-tag out">OUT</span>';
                header += '</div>';
            }
            header += '</div>';

            // Flights
            var flightsHtml = '';
            if (wd.flights && wd.flights.length) {
                wd.flights.forEach(function(f) {
                    flightsHtml += '<div class="cw-chip flight">' +
                        '&#9992; ' + f.flight_number + ' ' + f.route_from + '→' + f.route_to +
                        (f.depart_time ? ' <span class="cw-chip-time">' + f.depart_time + '</span>' : '') +
                        '</div>';
                });
            }

            // Transits
            var transitHtml = '';
            if (wd.transits && wd.transits.length) {
                wd.transits.forEach(function(t) {
                    transitHtml += '<div class="cw-chip transport">' +
                        '&#128644; ' + t.route_from + ' → ' + t.route_to +
                        (t.duration ? ' <span class="cw-chip-time">' + t.duration + '</span>' : '') +
                        '</div>';
                });
            }

            // Time slot swim lanes
            var slotsHtml = '<div class="cw-slots">';
            TIME_SLOTS.forEach(function(slot) {
                var slotActivities = wd.activities.filter(function(a) {
                    return a.time_slot === slot;
                });
                if (slotActivities.length === 0) return;

                slotsHtml += '<div class="cw-slot">';
                slotsHtml += '<div class="cw-slot-label">' + SLOT_LABELS[slot] + '</div>';
                slotsHtml += '<div class="cw-slot-items">';
                slotActivities.forEach(function(a) {
                    var cat = CAT_COLORS[a.category] || CAT_COLORS.transit;
                    var classes = 'cw-activity';
                    if (a.is_completed) classes += ' completed';
                    if (a.is_optional) classes += ' optional';

                    slotsHtml += '<div class="' + classes + '" style="border-left-color:' + cat.border + '">';
                    var title = a.title.length > 35 ? a.title.substring(0, 32) + '...' : a.title;
                    slotsHtml += '<span class="cw-act-title">' + title + '</span>';
                    if (a.start_time) slotsHtml += '<span class="cw-act-time">' + a.start_time + '</span>';
                    if (a.is_optional) slotsHtml += '<span class="cw-badge optional">Optional</span>';
                    if (a.book_ahead) slotsHtml += '<span class="cw-badge book">Book</span>';
                    if (a.is_completed) slotsHtml += '<span class="cw-badge done">&#10003;</span>';
                    slotsHtml += '</div>';
                });
                slotsHtml += '</div></div>';
            });
            slotsHtml += '</div>';

            dayEl.innerHTML = header + flightsHtml + transitHtml + slotsHtml;
            container.appendChild(dayEl);
        }

        // Update tab active state
        var tabs = document.querySelectorAll('.cal-week-tab');
        for (var i = 0; i < tabs.length; i++) {
            tabs[i].classList.toggle('active', parseInt(tabs[i].dataset.week) === week);
        }
    }

    document.querySelector('.cal-week-nav').addEventListener('click', function(e) {
        var tab = e.target.closest('.cal-week-tab');
        if (tab) buildWeekView(parseInt(tab.dataset.week));
    });

    // --- DAY VIEW ---
    var currentDayNum = D.todayDayNum || 1;

    function buildDayView(dn) {
        currentDayNum = dn;
        var wd = D.weekData[dn];
        if (!wd) return;

        var dateObj = new Date(wd.date + 'T12:00:00');
        var weekday = dateObj.toLocaleDateString('en-US', { weekday: 'long' });
        var monthDay = dateObj.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });

        // Update nav
        document.getElementById('calDayCurrent').innerHTML =
            '<span class="cd-weekday">' + weekday + ', ' + monthDay + '</span>' +
            '<span class="cd-daynum">Day ' + dn + '</span>';

        // Update prev/next button visibility
        document.getElementById('calDayPrev').style.visibility = dn > 1 ? 'visible' : 'hidden';
        document.getElementById('calDayNext').style.visibility = dn < 14 ? 'visible' : 'hidden';

        var detail = document.getElementById('calDayDetail');
        var html = '';

        // Day title
        html += '<div class="cdv-header">' +
            '<span class="cdv-emoji">' + wd.type_emoji + '</span> ' +
            '<span class="cdv-title">' + wd.title + '</span>' +
            '<span class="cdv-location">' + wd.location_name + '</span>' +
            '</div>';

        // Accommodation
        if (wd.accom_name) {
            html += '<div class="cdv-accom">';
            html += '<span class="cdv-accom-icon">&#127976;</span> '; // 🏨
            if (wd.accom_check_in) html += '<span class="cdv-accom-tag">Check-in</span> ';
            html += wd.accom_name;
            if (wd.accom_check_out && !wd.accom_check_in) html += ' <span class="cdv-accom-tag out">Check-out</span>';
            html += '</div>';
        }

        // Flights
        if (wd.flights && wd.flights.length) {
            wd.flights.forEach(function(f) {
                html += '<div class="cdv-flight">' +
                    '&#9992; <strong>' + f.flight_number + '</strong> ' +
                    f.route_from + ' → ' + f.route_to +
                    (f.depart_time ? ' at ' + f.depart_time : '') +
                    '</div>';
            });
        }

        // Transits
        if (wd.transits && wd.transits.length) {
            wd.transits.forEach(function(t) {
                html += '<div class="cdv-transit">' +
                    '&#128644; ' + t.route_from + ' → ' + t.route_to +
                    (t.duration ? ' (' + t.duration + ')' : '') +
                    (t.jr_covered ? ' <span class="cdv-jr">JR</span>' : '') +
                    '</div>';
            });
        }

        // Activities grouped by time slot
        html += '<div class="cdv-timeline">';
        TIME_SLOTS.forEach(function(slot) {
            var acts = wd.activities.filter(function(a) { return a.time_slot === slot; });
            if (acts.length === 0) return;

            html += '<div class="cdv-slot">';
            html += '<div class="cdv-slot-label">' + SLOT_LABELS[slot] + '</div>';
            html += '<div class="cdv-slot-acts">';
            acts.forEach(function(a) {
                var cat = CAT_COLORS[a.category] || CAT_COLORS.transit;
                var cls = 'cdv-act';
                if (a.is_completed) cls += ' completed';
                if (a.is_optional) cls += ' optional';

                html += '<div class="' + cls + '" style="border-left-color:' + cat.border + '">';
                html += '<div class="cdv-act-main">';
                if (a.is_completed) html += '<span class="cdv-done-check">&#10003;</span> ';
                html += '<span class="cdv-act-title">' + a.title + '</span>';
                html += '</div>';
                var meta = '';
                if (a.start_time) meta += '<span class="cdv-act-time">' + a.start_time + '</span>';
                if (a.is_optional) meta += '<span class="cdv-badge optional">Optional</span>';
                if (a.book_ahead) meta += '<span class="cdv-badge book">Book ahead</span>';
                if (a.is_confirmed) meta += '<span class="cdv-badge confirmed">Confirmed</span>';
                if (meta) html += '<div class="cdv-act-meta">' + meta + '</div>';
                html += '</div>';
            });
            html += '</div></div>';
        });
        html += '</div>';

        // Link to full day page
        html += '<a href="/day/' + dn + '" class="cdv-full-link">View full day details &rsaquo;</a>';

        detail.innerHTML = html;
    }

    document.getElementById('calDayPrev').addEventListener('click', function() {
        if (currentDayNum > 1) buildDayView(currentDayNum - 1);
    });
    document.getElementById('calDayNext').addEventListener('click', function() {
        if (currentDayNum < 14) buildDayView(currentDayNum + 1);
    });

    // Swipe support for day view
    var dayDetail = document.getElementById('calDayDetail');
    var touchStartX = 0;
    dayDetail.addEventListener('touchstart', function(e) {
        touchStartX = e.changedTouches[0].screenX;
    }, { passive: true });
    dayDetail.addEventListener('touchend', function(e) {
        var diff = e.changedTouches[0].screenX - touchStartX;
        if (Math.abs(diff) > 60) {
            if (diff > 0 && currentDayNum > 1) buildDayView(currentDayNum - 1);
            else if (diff < 0 && currentDayNum < 14) buildDayView(currentDayNum + 1);
        }
    }, { passive: true });

    // --- Initialize ---
    buildMonthGrid();
    buildWeekView(D.todayDayNum && D.todayDayNum > 7 ? 2 : 1);
    buildDayView(currentDayNum);
    switchView(currentView);

})();
