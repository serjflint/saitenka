local phase = 0
mp.observe_property("time-pos", "number", function(_, t)
    if not t then return end
    if phase == 0 and t >= 6.5 then
        phase = 1
        mp.commandv("script-message", "saitenka-sub-next")
    elseif phase == 1 and t >= 8.5 then
        phase = 2
        mp.commandv("script-message", "saitenka-sub-replay")
    elseif phase == 2 and t >= 9 then
        phase = 3
        mp.commandv("script-message", "saitenka-sub-prev")
    end
end)
