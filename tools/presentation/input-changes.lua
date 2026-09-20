local phase = 0
local resumed, restored = false, false
mp.observe_property("time-pos", "number", function(_, t)
    if not t then return end
    if phase == 0 and t >= 6.3 then
        phase = 1
        mp.set_property_bool("pause", true)
        mp.add_timeout(0.2, function()
            assert(mp.set_property_bool("pause", false))
            resumed = true
        end)
    elseif phase == 1 and t >= 6.5 then
        phase = 2
        mp.set_property_number("sub-delay", -1)
    elseif phase == 2 and t >= 8.5 then
        phase = 3
        mp.commandv("script-message", "saitenka-toggle-overlay")
        mp.add_timeout(0.3, function() mp.commandv("script-message", "saitenka-toggle-overlay") end)
    elseif phase == 3 and t >= 10 then
        phase = 4
        mp.set_property_number("window-scale", 0.8)
    elseif phase == 4 and t >= 10.5 then
        phase = 5
        local sid = mp.get_property("sid")
        mp.set_property("sid", "no")
        mp.add_timeout(0.3, function()
            assert(mp.set_property("sid", sid))
            restored = true
        end)
    elseif phase == 5 and t >= 19 and resumed and restored then
        phase = 6
        mp.msg.info("saitenka-presentation-inputs-complete")
    end
end)
