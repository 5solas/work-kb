dofile(vim.fs.dirname(debug.getinfo(1, "S").source:sub(2)) .. "/repro_common.lua")
vim.g.mapleader = ","
require("work-kb").setup({ root = REPO, prefix = "<leader>k", server = { enable = false } })
vim.g.mapleader = " "
require("work-kb").setup({ root = REPO, prefix = false, server = { enable = false } }) -- user turns keys off
P("after prefix=false, ',kc' still mapped:", vim.fn.maparg(",kc", "n") ~= "", vim.fn.maparg(",kc", "n", false, true).desc)
require("work-kb").setup({ root = REPO, prefix = "<F8>", server = { enable = false } })
P("after prefix=<F8>, ',kc' still mapped:", vim.fn.maparg(",kc", "n") ~= "", " <F8>c:", vim.fn.maparg("<F8>c", "n", false, true).desc)
VERDICT(vim.fn.maparg(",kc", "n") ~= "", "keys bound under the old mapleader stay after setup() changes prefix")
